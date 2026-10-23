"""Creating the nested sheets in the Fusion design."""

import csv
import os
import re

import adsk.core
import adsk.fusion

from .sn_parts import ATTR_GROUP, ATTR_LAYOUT

SHEET_GAP = 20.0  # cm between sheets in the layout


class LayoutOptions:
    def __init__(self):
        self.replace_previous = True
        self.stock_body = True
        self.labels = True
        self.outline_sketch = True
        self.margin = 1.0


def delete_previous_layouts(design: adsk.fusion.Design):
    root = design.rootComponent
    doomed = [occ for occ in root.occurrences
              if occ.component.attributes.itemByName(ATTR_GROUP, ATTR_LAYOUT)]
    for occ in doomed:
        occ.deleteMe()
    return len(doomed)


def build(design: adsk.fusion.Design, groups, source_bodies, opt):
    """Create the layout. `groups` is a list of (thickness, NestResult).

    Returns {(group index, sheet index): sheet component} and a list of warnings."""
    root = design.rootComponent
    parametric = design.designType == adsk.fusion.DesignTypes.ParametricDesignType
    tbm = adsk.fusion.TemporaryBRepManager.get()
    units = design.unitsManager
    warnings = []

    if opt.replace_previous:
        delete_previous_layouts(design)

    # Put the layout next to the model so it doesn't overlap it.
    origin = adsk.core.Matrix3D.create()
    try:
        box = None
        for b in source_bodies:
            bb = b.boundingBox
            if box is None:
                box = bb.copy()
            else:
                box.combine(bb)
        if box:
            origin.translation = adsk.core.Vector3D.create(box.maxPoint.x + SHEET_GAP, box.minPoint.y, box.minPoint.z)
    except Exception:
        pass

    layout_occ = root.occurrences.addNewComponent(origin)
    layout = layout_occ.component
    layout.name = 'Nest Layout'
    layout.attributes.add(ATTR_GROUP, ATTR_LAYOUT, '1')

    sheets = {}
    y_offset = 0.0
    for gi, (thickness, result) in enumerate(groups):
        L, W = result.sheet_length, result.sheet_width
        tname = units.formatValue(thickness)
        for sheet in result.sheets:
            m = adsk.core.Matrix3D.create()
            m.translation = adsk.core.Vector3D.create((sheet.index - 1) * (L + SHEET_GAP), y_offset, 0)
            occ = layout.occurrences.addNewComponent(m)
            comp = occ.component
            comp.name = 'Sheet %d - %s' % (sheet.index, tname)
            sheets[(gi, sheet.index)] = comp

            temps = []
            for pl in sheet.placements:
                info, _ = pl.part.data
                body = tbm.copy(info.temp)
                tbm.transform(body, info.matrix(pl.rotation, pl.x, pl.y))
                _drop_to_sheet(tbm, body)
                temps.append((pl.part.name, body))
            if opt.stock_body:
                box = adsk.core.OrientedBoundingBox3D.create(
                    adsk.core.Point3D.create(L / 2, W / 2, thickness / 2),
                    adsk.core.Vector3D.create(1, 0, 0), adsk.core.Vector3D.create(0, 1, 0),
                    L, W, thickness)
                temps.append(('Stock', tbm.createBox(box)))

            added = _add_bodies(comp, temps, parametric)
            stock = added.get('Stock')
            if stock:
                stock.isLightBulbOn = False

            try:
                _sheet_sketch(comp, sheet, result, thickness, tname, opt)
            except Exception as e:
                warnings.append('Sheet %d: could not draw the sheet sketch (%s)' % (sheet.index, e))
            if opt.outline_sketch:
                try:
                    _outline_sketch(comp, [b for n, b in added.items() if n != 'Stock'])
                except Exception as e:
                    warnings.append('Sheet %d: could not project outlines (%s)' % (sheet.index, e))
            if opt.labels:
                try:
                    _label_sketch(comp, sheet, thickness)
                except Exception as e:
                    warnings.append('Sheet %d: could not add labels (%s)' % (sheet.index, e))
        y_offset += W + SHEET_GAP
    return layout_occ, sheets, warnings


def _drop_to_sheet(tbm: adsk.fusion.TemporaryBRepManager, body: adsk.fusion.BRepBody):
    """Shift the body so its lowest point is exactly on the sheet (z = 0)."""
    try:
        bb = body.preciseBoundingBox
    except Exception:
        bb = body.boundingBox
    dz = -bb.minPoint.z
    if abs(dz) > 1e-7:
        m = adsk.core.Matrix3D.create()
        m.translation = adsk.core.Vector3D.create(0, 0, dz)
        tbm.transform(body, m)


def _add_bodies(comp: adsk.fusion.Component, temps, parametric):
    """Add temporary bodies to `comp`. Returns {name: body} (unique names)."""
    base = None
    if parametric:
        base = comp.features.baseFeatures.add()
        base.startEdit()
    added = {}
    try:
        for name, temp in temps:
            body = comp.bRepBodies.add(temp, base) if base else comp.bRepBodies.add(temp)
            unique, n = name, 2
            while unique in added:
                unique = '%s (%d)' % (name, n)
                n += 1
            body.name = unique
            added[unique] = None
    finally:
        if base:
            base.finishEdit()
    # Body references made while editing a base feature are not valid after
    # finishEdit, so look them up again by name.
    for name in list(added):
        added[name] = comp.bRepBodies.itemByName(name)
    return {k: v for k, v in added.items() if v}


def _sheet_sketch(comp: adsk.fusion.Component, sheet, result, thickness, tname, opt):
    L, W = result.sheet_length, result.sheet_width
    sk = comp.sketches.add(comp.xYConstructionPlane)
    sk.name = 'Sheet %d outline' % sheet.index
    sk.isComputeDeferred = True
    P = adsk.core.Point3D.create
    sk.sketchCurves.sketchLines.addTwoPointRectangle(P(0, 0, 0), P(L, W, 0))
    m = opt.margin
    if m > 0 and 2 * m < min(L, W):
        lines = sk.sketchCurves.sketchLines.addTwoPointRectangle(P(m, m, 0), P(L - m, W - m, 0))
        for line in lines:
            line.isConstruction = True
    height = max(min(W * 0.04, 6.0), 1.0)
    title = 'Sheet %d  |  %s  |  %d parts  |  %.0f%% used' % (
        sheet.index, tname, len(sheet.placements), sheet.utilization * 100)
    _add_text(sk, title, P(0, W + height * 0.5, 0), P(L, W + height * 2.0, 0), height,
              adsk.core.HorizontalAlignments.LeftHorizontalAlignment)
    sk.isComputeDeferred = False


def _outline_sketch(comp: adsk.fusion.Component, bodies):
    sk = comp.sketches.add(comp.xYConstructionPlane)
    sk.name = 'Cut outlines'
    sk.isComputeDeferred = True
    for body in bodies:
        sk.project2([body], False)
    sk.isComputeDeferred = False
    return sk


def _label_sketch(comp: adsk.fusion.Component, sheet, thickness):
    planes = comp.constructionPlanes
    pin = planes.createInput()
    pin.setByOffset(comp.xYConstructionPlane, adsk.core.ValueInput.createByReal(thickness))
    plane = planes.add(pin)
    plane.name = 'Top of sheet'
    plane.isLightBulbOn = False
    sk = comp.sketches.add(plane)
    sk.name = 'Labels'
    sk.isComputeDeferred = True
    def P(x, y):
        return sk.modelToSketchSpace(adsk.core.Point3D.create(x, y, thickness))

    for pl in sheet.placements:
        x0, y0, x1, y1 = pl.bbox
        w, h = x1 - x0, y1 - y0
        height = max(min(min(w, h) * 0.12, 4.0), 0.4)
        _add_text(sk, pl.part.name, P(x0, y0), P(x1, y1), height,
                  adsk.core.HorizontalAlignments.CenterHorizontalAlignment)
    sk.isComputeDeferred = False


def _add_text(sketch: adsk.fusion.Sketch, text, corner, diagonal, height, halign):
    safe = text.replace("'", '`')
    ti = sketch.sketchTexts.createInput3("'%s'" % safe, adsk.core.ValueInput.createByReal(height))
    ti.setAsMultiLine(corner, diagonal, halign, adsk.core.VerticalAlignments.MiddleVerticalAlignment, 0)
    sketch.sketchTexts.add(ti)


# ------------------------------------------------------------------ export

def export_files(design: adsk.fusion.Design, folder, groups, sheets: 'dict[tuple, adsk.fusion.Component]'):
    """DXF of each sheet's cut outlines plus a CSV cut list. Returns file paths."""
    units = design.unitsManager
    em = design.exportManager
    stem = _safe(design.parentDocument.name if design.parentDocument else 'layout')
    written = []
    for (gi, index), comp in sorted(sheets.items()):
        thickness = groups[gi][0]
        sk = comp.sketches.itemByName('Cut outlines')
        if not sk:
            continue
        path = os.path.join(folder, '%s_%s_sheet%d.dxf' % (stem, _safe(units.formatValue(thickness)), index))
        em.execute(em.createDXFSketchExportOptions(path, sk))
        written.append(path)

    path = os.path.join(folder, '%s_cutlist.csv' % stem)
    with open(path, 'w', newline='', encoding='utf-8') as f:
        out = csv.writer(f)
        out.writerow(['Part', 'Thickness', 'Length', 'Width', 'Sheet', 'X', 'Y', 'Rotation'])
        for thickness, result in groups:
            for sheet in result.sheets:
                for pl in sheet.placements:
                    info, _ = pl.part.data
                    out.writerow([pl.part.name, units.formatValue(thickness),
                                  units.formatValue(info.size[0]), units.formatValue(info.size[1]),
                                  sheet.index, units.formatValue(pl.bbox[0]), units.formatValue(pl.bbox[1]),
                                  '%g' % pl.rotation])
            for part in result.unplaced:
                out.writerow([part.name, units.formatValue(thickness), '', '', 'DOES NOT FIT', '', '', ''])
    written.append(path)
    return written


def _safe(text):
    return re.sub(r'[^A-Za-z0-9._-]+', '_', text).strip('_') or 'layout'
