"""The "Nest Parts on Sheet" command: dialog, preview and execution."""

import json
import os
import sys
import traceback

import adsk.core
import adsk.fusion

from . import sn_layout, sn_parts
from .nestcore import NestOptions, nest

CMD_ID = 'SheetNester_NestParts'
CMD_NAME = 'Nest Parts on Sheet'
CMD_TOOLTIP = ('Lay flat panels (MDF, plywood) on a sheet and optimise the layout for cutting.\n'
               'Creates a "Nest Layout" component with one sub-component per sheet.')
WORKSPACE_ID = 'FusionSolidEnvironment'
PANEL_ID = 'SolidScriptsAddinsPanel'

HERE = os.path.dirname(os.path.abspath(__file__))
ICON_FOLDER = os.path.join(HERE, 'resources', '')
SETTINGS_FILE = os.path.join(HERE, 'settings.json')

# (label, length cm, width cm)
SHEET_PRESETS = [
    ('2440 x 1220 mm', 244.0, 122.0),
    ('2500 x 1250 mm', 250.0, 125.0),
    ('3050 x 1220 mm', 305.0, 122.0),
    ('2800 x 2070 mm', 280.0, 207.0),
    ('1525 x 1525 mm (Baltic birch)', 152.5, 152.5),
    ('8 x 4 ft', 243.84, 121.92),
    ('5 x 5 ft', 152.4, 152.4),
]
CUSTOM = 'Custom'

FILL = [('Along the long side', 'long'), ('Along the short side', 'short')]
ROTATION = [('Free (0/90/180/270)', (0, 90, 180, 270)),
            ('Lengthwise only (grain along sheet)', (0, 180)),
            ('Crosswise only', (90, 270))]
METHOD = [('Best of both', 'best'), ('Rectangular (bounding boxes)', 'rect'), ('True shape', 'shape')]
EFFORT = [('Quick', 1.5, 1500), ('Normal', 5.0, 2500), ('Thorough', 20.0, 4000)]

DEFAULTS = {
    'preset': SHEET_PRESETS[0][0], 'sheetLength': 244.0, 'sheetWidth': 122.0, 'margin': 1.0,
    'toolDiameter': 0.6, 'extraSpacing': 0.0,
    'fill': FILL[0][0], 'rotation': ROTATION[0][0], 'method': METHOD[0][0], 'effort': EFFORT[1][0],
    'copies': 1, 'largestDown': True,
    'replace': True, 'stock': True, 'labels': True, 'outlines': True, 'export': False, 'preview': False,
}

_app = adsk.core.Application.get()
_ui = _app.userInterface
_handlers = []          # keep handler objects alive
_cmd_handlers = []      # per-command-instance handlers
_state = {}
_pending = {}           # results waiting to be reported once the command has finished

# Fired at the end of execute. Fusion handles it once it is idle, i.e. after
# the command has committed, so the summary dialog (and the DXF folder
# dialog) don't keep the command - and its busy cursor - running.
AFTER_NEST_EVENT = 'SheetNester_AfterNest'


# ----------------------------------------------------------------- add-in

def start():
    cmd_def = _ui.commandDefinitions.itemById(CMD_ID)
    if cmd_def:
        cmd_def.deleteMe()
    folder = ICON_FOLDER if os.path.isfile(os.path.join(ICON_FOLDER, '32x32.png')) else ''
    cmd_def = _ui.commandDefinitions.addButtonDefinition(CMD_ID, CMD_NAME, CMD_TOOLTIP, folder)
    _add_handler(cmd_def.commandCreated, _on_created, _handlers)
    panel = _ui.workspaces.itemById(WORKSPACE_ID).toolbarPanels.itemById(PANEL_ID)
    control = panel.controls.itemById(CMD_ID) or panel.controls.addCommand(cmd_def)
    control.isPromoted = True
    try:
        _app.unregisterCustomEvent(AFTER_NEST_EVENT)  # left over from a previous load
    except Exception:
        pass
    _add_handler(_app.registerCustomEvent(AFTER_NEST_EVENT), _on_after_nest, _handlers)


def stop():
    _app.unregisterCustomEvent(AFTER_NEST_EVENT)
    _pending.clear()
    panel = _ui.workspaces.itemById(WORKSPACE_ID).toolbarPanels.itemById(PANEL_ID)
    control = panel.controls.itemById(CMD_ID) if panel else None
    if control:
        control.deleteMe()
    cmd_def = _ui.commandDefinitions.itemById(CMD_ID)
    if cmd_def:
        cmd_def.deleteMe()
    _handlers.clear()
    _cmd_handlers.clear()


def _add_handler(event, callback, store):
    """Connect `callback` to a Fusion event (same approach as Fusion's add-in template)."""
    module = sys.modules[event.__module__]
    handler_type = module.__dict__[event.add.__annotations__['handler']]

    class Handler(handler_type):
        def __init__(self):
            super().__init__()

        def notify(self, args):
            try:
                callback(args)
            except Exception:
                _ui.messageBox('Sheet Nester failed:\n\n%s' % traceback.format_exc())

    handler = Handler()
    event.add(handler)
    store.append(handler)
    return handler


# --------------------------------------------------------------- settings

def _load_settings():
    cfg = dict(DEFAULTS)
    try:
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            saved = json.load(f)
        cfg.update({k: v for k, v in saved.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return cfg


def _save_settings(cfg):
    try:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump({k: cfg[k] for k in DEFAULTS}, f, indent=2)
    except OSError:
        pass


# ----------------------------------------------------------------- dialog

def _on_created(args: adsk.core.CommandCreatedEventArgs):
    design = adsk.fusion.Design.cast(_app.activeProduct)
    if not design:
        _ui.messageBox('Switch to the Design workspace to nest parts.')
        return
    cmd = args.command
    cmd.okButtonText = 'Nest'
    cmd.setDialogInitialSize(420, 640)
    _state.clear()
    _state['cache'] = {}
    _state['busy'] = False
    _cmd_handlers.clear()

    cfg = _load_settings()
    units = design.unitsManager.defaultLengthUnits
    inputs = cmd.commandInputs

    sel = inputs.addSelectionInput('parts', 'Parts', 'Select bodies or components to nest')
    sel.addSelectionFilter('SolidBodies')
    sel.addSelectionFilter('Occurrences')
    sel.setSelectionLimits(0)
    inputs.addBoolValueInput('sameThickness', 'Add all with same thickness', True, '', False)
    info = inputs.addTextBoxCommandInput('info', '', 'Select the panels to cut.', 2, True)
    info.isFullWidth = True

    grp = inputs.addGroupCommandInput('sheetGroup', 'Sheet').children
    dd = grp.addDropDownCommandInput('preset', 'Sheet size', adsk.core.DropDownStyles.TextListDropDownStyle)
    for label, _, _ in SHEET_PRESETS:
        dd.listItems.add(label, label == cfg['preset'])
    dd.listItems.add(CUSTOM, cfg['preset'] == CUSTOM)
    if not dd.selectedItem:
        dd.listItems.item(dd.listItems.count - 1).isSelected = True
    grp.addValueInput('sheetLength', 'Length', units, adsk.core.ValueInput.createByReal(cfg['sheetLength']))
    grp.addValueInput('sheetWidth', 'Width', units, adsk.core.ValueInput.createByReal(cfg['sheetWidth']))
    m = grp.addValueInput('margin', 'Edge margin', units, adsk.core.ValueInput.createByReal(cfg['margin']))
    m.tooltip = 'Minimum distance from the parts to the edge of the sheet.'

    grp = inputs.addGroupCommandInput('cutGroup', 'Cutting').children
    t = grp.addValueInput('toolDiameter', 'Tool diameter', units, adsk.core.ValueInput.createByReal(cfg['toolDiameter']))
    t.tooltip = 'Diameter of the router bit (or saw kerf). Parts are spaced at least this far apart.'
    e = grp.addValueInput('extraSpacing', 'Extra spacing', units, adsk.core.ValueInput.createByReal(cfg['extraSpacing']))
    e.tooltip = 'Additional clearance between parts on top of the tool diameter.'

    grp = inputs.addGroupCommandInput('layoutGroup', 'Layout').children
    _dropdown(grp, 'fill', 'Fill direction', [f[0] for f in FILL], cfg['fill'],
              'Long side: parts fill a band along the long edge, leaving a full-length offcut strip.\n'
              'Short side: parts gather at one end, leaving a full-width offcut at the other end.')
    _dropdown(grp, 'rotation', 'Part rotation', [r[0] for r in ROTATION], cfg['rotation'],
              'Lengthwise keeps each part\'s long side along the sheet\'s long side (follows the grain).')
    _dropdown(grp, 'method', 'Nesting method', [mm[0] for mm in METHOD], cfg['method'],
              'Rectangular packs bounding boxes (best for square panels).\n'
              'True shape uses the real outline: angled parts interlock and small parts can go inside cut-outs.\n'
              'Best of both tries each and keeps the layout that uses fewer sheets / leaves the larger offcut.')
    _dropdown(grp, 'effort', 'Optimisation', [ef[0] for ef in EFFORT], cfg['effort'],
              'How long to search for a better layout (about 1.5 s, 5 s or 20 s per thickness).')
    grp.addIntegerSpinnerCommandInput('copies', 'Copies of each part', 1, 100, 1, int(cfg['copies']))
    ld = grp.addBoolValueInput('largestDown', 'Largest face down', True, '', bool(cfg['largestDown']))
    ld.tooltip = 'Lay each part on its larger face so chamfers and bevels face up (machinable from the top).'

    grp = inputs.addGroupCommandInput('outputGroup', 'Output').children
    grp.addBoolValueInput('replace', 'Replace previous layout', True, '', bool(cfg['replace']))
    grp.addBoolValueInput('stock', 'Stock body (for CAM)', True, '', bool(cfg['stock']))
    grp.addBoolValueInput('outlines', 'Cut outline sketches', True, '', bool(cfg['outlines']))
    grp.addBoolValueInput('labels', 'Part labels', True, '', bool(cfg['labels']))
    ex = grp.addBoolValueInput('export', 'Export DXF + cut list', True, '', bool(cfg['export']))
    ex.tooltip = 'After nesting, choose a folder for one DXF per sheet and a CSV cut list.'
    grp.addBoolValueInput('preview', 'Live preview', True, '', bool(cfg['preview']))

    _add_handler(cmd.inputChanged, _on_input_changed, _cmd_handlers)
    _add_handler(cmd.validateInputs, _on_validate, _cmd_handlers)
    _add_handler(cmd.executePreview, _on_preview, _cmd_handlers)
    _add_handler(cmd.execute, _on_execute, _cmd_handlers)
    _add_handler(cmd.destroy, _on_destroy, _cmd_handlers)


def _dropdown(inputs: adsk.core.CommandInputs, id_, name, labels, selected, tooltip):
    dd = inputs.addDropDownCommandInput(id_, name, adsk.core.DropDownStyles.TextListDropDownStyle)
    for label in labels:
        dd.listItems.add(label, label == selected)
    if not dd.selectedItem:
        dd.listItems.item(0).isSelected = True
    dd.tooltip = tooltip
    return dd


def _read(inputs: adsk.core.CommandInputs):
    cfg = {}
    for key in ('sheetLength', 'sheetWidth', 'margin', 'toolDiameter', 'extraSpacing'):
        cfg[key] = inputs.itemById(key).value
    for key in ('preset', 'fill', 'rotation', 'method', 'effort'):
        cfg[key] = inputs.itemById(key).selectedItem.name
    cfg['copies'] = inputs.itemById('copies').value
    for key in ('largestDown', 'replace', 'stock', 'labels', 'outlines', 'export', 'preview', 'sameThickness'):
        cfg[key] = inputs.itemById(key).value
    sel = inputs.itemById('parts')
    cfg['entities'] = [sel.selection(i).entity for i in range(sel.selectionCount)]
    return cfg


def _on_input_changed(args: adsk.core.InputChangedEventArgs):
    if _state.get('busy'):
        return
    inp = args.input
    inputs = args.inputs
    _state['busy'] = True
    try:
        if inp.id == 'preset':
            for label, length, width in SHEET_PRESETS:
                if label == inp.selectedItem.name:
                    inputs.itemById('sheetLength').value = length
                    inputs.itemById('sheetWidth').value = width
        elif inp.id in ('sheetLength', 'sheetWidth'):
            length = inputs.itemById('sheetLength').value
            width = inputs.itemById('sheetWidth').value
            match = CUSTOM
            for label, pl, pw in SHEET_PRESETS:
                if {round(pl, 4), round(pw, 4)} == {round(length, 4), round(width, 4)}:
                    match = label
            dd = inputs.itemById('preset')
            for item in dd.listItems:
                if item.name == match and not item.isSelected:
                    item.isSelected = True
        elif inp.id == 'sameThickness' and inp.value:
            _add_same_thickness(inputs)
        if inp.id in ('parts', 'sameThickness'):
            _update_info(inputs)
    finally:
        _state['busy'] = False


def _add_same_thickness(inputs: adsk.core.CommandInputs):
    design = adsk.fusion.Design.cast(_app.activeProduct)
    sel = inputs.itemById('parts')
    selected = sn_parts.collect_bodies([sel.selection(i).entity for i in range(sel.selectionCount)])
    targets = {t for t in (sn_parts.thickness_of(b) for b in selected) if t}
    if not targets:
        inputs.itemById('info').formattedText = 'Select at least one panel first, then tick this box.'
        inputs.itemById('sameThickness').value = False
        return
    have = {b.entityToken for b in selected}
    for body in sn_parts.all_design_bodies(design):
        if body.entityToken in have:
            continue
        t = sn_parts.thickness_of(body)
        if t and any(abs(t - x) <= sn_parts.THICKNESS_TOL for x in targets):
            sel.addSelection(body)


def _update_info(inputs: adsk.core.CommandInputs):
    design = adsk.fusion.Design.cast(_app.activeProduct)
    sel = inputs.itemById('parts')
    bodies = sn_parts.collect_bodies([sel.selection(i).entity for i in range(sel.selectionCount)])
    box = inputs.itemById('info')
    if not bodies:
        box.formattedText = 'Select the panels to cut.'
        return
    groups = _group_by_thickness([(b, sn_parts.thickness_of(b)) for b in bodies])
    fmt = design.unitsManager.formatValue
    parts = ', '.join('%s (%d)' % (fmt(t), len(items)) for t, items in groups if t is not None)
    text = '%d part%s: %s' % (len(bodies), '' if len(bodies) == 1 else 's', parts)
    if len([g for g in groups if g[0] is not None]) > 1:
        text += '<br>Each thickness is nested on its own sheets.'
    if any(t is None for t, _ in groups):
        text += '<br>Some bodies are not flat panels and will be skipped.'
    box.formattedText = text


def _group_by_thickness(pairs):
    """[(item, thickness)] -> [(thickness, [items])] sorted thickest first."""
    groups = []
    for item, t in pairs:
        for g in groups:
            if t is not None and g[0] is not None and abs(g[0] - t) <= sn_parts.THICKNESS_TOL:
                g[1].append(item)
                break
            if t is None and g[0] is None:
                g[1].append(item)
                break
        else:
            groups.append((t, [item]))
    return sorted(groups, key=lambda g: -(g[0] or 0))


def _on_validate(args: adsk.core.ValidateInputsEventArgs):
    inputs = args.inputs
    try:
        length = inputs.itemById('sheetLength').value
        width = inputs.itemById('sheetWidth').value
        margin = inputs.itemById('margin').value
        ok = (inputs.itemById('parts').selectionCount > 0 and length > 0 and width > 0 and margin >= 0 and
              inputs.itemById('toolDiameter').value >= 0 and inputs.itemById('extraSpacing').value >= 0 and
              2 * margin < min(length, width))
    except Exception:
        ok = False
    args.areInputsValid = ok


def _on_preview(args: adsk.core.CommandEventArgs):
    cfg = _read(args.command.commandInputs)
    if not cfg['preview'] or not cfg['entities']:
        return
    _run(cfg, preview=True)


def _on_execute(args: adsk.core.CommandEventArgs):
    cfg = _read(args.command.commandInputs)
    _save_settings(cfg)
    _run(cfg, preview=False)


def _on_destroy(args: adsk.core.CommandEventArgs):
    _state.clear()
    _cmd_handlers.clear()


# ---------------------------------------------------------------- running

def _run(cfg, preview):
    design = adsk.fusion.Design.cast(_app.activeProduct)
    fmt = design.unitsManager.formatValue
    bodies = sn_parts.collect_bodies(cfg['entities'])

    # Analyse (cached per body for the life of the dialog).
    cache = _state.setdefault('cache', {})
    infos, skipped = [], []
    for body in bodies:
        key = (body.entityToken, cfg['largestDown'])
        if key not in cache:
            try:
                cache[key] = sn_parts.analyze(body, cfg['largestDown'])
            except Exception as e:
                cache[key] = str(e) or e.__class__.__name__
        info = cache[key]
        if isinstance(info, str):
            skipped.append('%s: %s' % (sn_parts.part_name(body), info))
        else:
            infos.append(info)
    if not infos:
        if not preview:
            _report_later(message='None of the selected bodies could be laid flat.\n\n' + '\n'.join(skipped))
        return

    groups = _group_by_thickness([(i, i.thickness) for i in infos])
    effort = next(e for e in EFFORT if e[0] == cfg['effort'])
    budget, resolution = effort[1], effort[2]
    if preview:
        budget = min(budget, 1.0)
        resolution = min(resolution, 1500)

    progress = None
    if not preview:
        progress = _ui.createProgressDialog()
        progress.isCancelButtonShown = True
        progress.show('Sheet Nester', 'Nesting...', 0, 100, 0)

    results = []
    try:
        for gi, (thickness, items) in enumerate(groups):
            parts = [info.nest_part(c) for info in items for c in range(int(cfg['copies']))]

            def report(frac, message, gi=gi, thickness=thickness):
                if progress is None:
                    return True
                progress.progressValue = int(100 * (gi + frac) / len(groups))
                progress.message = '%s - %s' % (fmt(thickness), message)
                adsk.doEvents()
                return not progress.wasCancelled

            opt = NestOptions(
                sheet_length=cfg['sheetLength'], sheet_width=cfg['sheetWidth'], margin=cfg['margin'],
                spacing=cfg['toolDiameter'] + cfg['extraSpacing'], tolerance=0.005,
                fill=dict(FILL)[cfg['fill']], rotations=dict(ROTATION)[cfg['rotation']],
                method=dict(METHOD)[cfg['method']], time_budget=budget, resolution=resolution,
                progress=report)
            results.append((thickness, nest(parts, opt)))
            if results[-1][1].cancelled:
                break
    finally:
        if progress:
            progress.hide()

    lo = sn_layout.LayoutOptions()
    lo.replace_previous = cfg['replace']
    lo.stock_body = cfg['stock']
    lo.labels = cfg['labels'] and not preview
    lo.outline_sketch = cfg['outlines'] and not preview
    lo.margin = cfg['margin']
    _, sheets, warnings = sn_layout.build(design, results, bodies, lo)

    if preview:
        return

    _report_later(design=design, cfg=cfg, results=results, skipped=skipped,
                  warnings=warnings, sheets=sheets)


def _report_later(**info):
    _pending.clear()
    _pending.update(info)
    _app.fireCustomEvent(AFTER_NEST_EVENT)


def _on_after_nest(args: adsk.core.CustomEventArgs):
    info = dict(_pending)
    _pending.clear()
    if 'message' in info:
        _ui.messageBox(info['message'], 'Sheet Nester')
        return
    if not info:
        return
    design, cfg, results = info['design'], info['cfg'], info['results']
    warnings, sheets = info['warnings'], info['sheets']
    files = []
    if cfg['export'] and sheets:
        dlg = _ui.createFolderDialog()
        dlg.title = 'Folder for DXF files and cut list'
        if dlg.showDialog() == adsk.core.DialogResults.DialogOK:
            try:
                files = sn_layout.export_files(design, dlg.folder, results, sheets)
            except Exception as e:
                warnings.append('Export failed: %s' % e)
    _ui.messageBox(_summary(cfg, results, info['skipped'], warnings, files, design.unitsManager.formatValue),
                   'Sheet Nester')


def _summary(cfg, results, skipped, warnings, files, fmt):
    lines = []
    total_sheets = sum(len(r.sheets) for _, r in results)
    total_parts = sum(len(s.placements) for _, r in results for s in r.sheets)
    if results:
        L, W = results[0][1].sheet_length, results[0][1].sheet_width
        lines.append('Nested %d parts on %d sheet%s of %s x %s.' % (
            total_parts, total_sheets, '' if total_sheets == 1 else 's', fmt(L), fmt(W)))
    engine_names = {'rect': 'rectangular', 'shape': 'true shape', 'best': 'best of both'}
    for thickness, r in results:
        used = ', '.join('#%d %.0f%%' % (s.index, s.utilization * 100) for s in r.sheets)
        lines.append('')
        lines.append('%s: %d sheet%s (%s) - %s layout, %d tries%s' % (
            fmt(thickness), len(r.sheets), '' if len(r.sheets) == 1 else 's', used or 'none',
            engine_names.get(r.method, r.method), r.attempts, ' - stopped early' if r.cancelled else ''))
        if r.unplaced:
            lines.append('  Too large for the sheet: ' + ', '.join(p.name for p in r.unplaced))
    if skipped:
        lines.append('')
        lines.append('Skipped:')
        lines.extend('  ' + s for s in skipped)
    if warnings:
        lines.append('')
        lines.extend(warnings)
    if files:
        lines.append('')
        lines.append('Exported %d files to %s' % (len(files), os.path.dirname(files[0])))
    return '\n'.join(lines)
