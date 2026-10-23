# Sheet Nester for Autodesk Fusion

A Fusion add-in that lays flat panels (MDF, plywood, etc.) on stock sheets and nests them for cutting. Select the bodies or components of a model, such as a speaker cabinet, set the sheet size and tool diameter, and it builds a **Nest Layout** with one component per sheet. Each sheet holds real copies of your parts lying flat, ready for 2D CAM.

## Installing

1. In Fusion, open **Utilities → Add-Ins → Scripts and Add-Ins** (or press `Shift+S`).
2. On the **Add-Ins** tab, click the green **+** and choose the folder `W:\Fusion_Nesting_plugin\SheetNester`.
3. Select **SheetNester** and click **Run**. Tick **Run on Startup** to load it automatically.

The command **Nest Parts on Sheet** appears in the **Design** workspace under **Utilities → Add-Ins**, and it's promoted to the toolbar.

## Using it

| Input | What it does |
|---|---|
| **Parts** | Bodies or components to cut. A component contributes all its visible bodies, including those in sub-components. |
| **Add all with same thickness** | Adds every visible body in the design that has the same thickness as the parts already selected. |
| **Sheet size / Length / Width** | Presets for common sizes (2440×1220, 2500×1250, 8×4 ft, 5×5 ft, …), or any custom size. |
| **Edge margin** | Minimum distance from the parts to the edge of the sheet. |
| **Tool diameter** | Router bit diameter (or saw kerf). Parts are always at least this far apart. |
| **Extra spacing** | Additional clearance on top of the tool diameter, e.g. for tabs or onion-skin passes. |
| **Fill direction** | **Along the long side** fills a band along the long edge and leaves a full-length strip. **Along the short side** packs parts at one end and leaves a full-width offcut at the other. |
| **Part rotation** | **Free** allows 0/90/180/270°. **Lengthwise only** keeps each part's long side along the sheet's long side, which follows the grain on plywood. **Crosswise only** is the opposite. |
| **Nesting method** | **Rectangular** packs bounding boxes, which works best for square panels. **True shape** uses the real outline: angled parts interlock and small parts can go inside large cut-outs. **Best of both** runs both and keeps the better result. |
| **Optimisation** | How long to search for a better layout: Quick ≈ 1.5 s, Normal ≈ 5 s, Thorough ≈ 20 s per thickness. A progress bar with **Cancel** is shown. |
| **Copies of each part** | Nest several sets at once, e.g. a stereo pair of speakers. |
| **Largest face down** | Lays each part on its larger face, so chamfers, bevels, round-overs and rear-chamfered driver holes face up and can be machined from the top. |
| **Replace previous layout** | Deletes the previous Nest Layout before creating the new one. |
| **Stock body (for CAM)** | Adds a hidden sheet-sized body per sheet. Use it as "stock from solid" in a CAM setup. |
| **Cut outline sketches** | A *Cut outlines* sketch per sheet with each part's projected silhouette. |
| **Part labels** | Part names as sketch text on top of each part. |
| **Export DXF + cut list** | After nesting, asks for a folder and writes one DXF per sheet (the cut outlines, in sheet coordinates) and a CSV cut list. |
| **Live preview** | Shows the layout while you change settings. It uses a shorter search, and labels and outlines are skipped. |

Your settings are remembered between sessions in `SheetNester/settings.json`.

### What gets created

```
Nest Layout                     (placed beside your model; never modifies the original)
├─ Sheet 1 - 18 mm
│   ├─ bodies: one flat copy per part, named after the part
│   ├─ Stock                    (hidden)
│   ├─ Sheet 1 outline          (sheet border, margin as construction lines, info text)
│   ├─ Cut outlines             (silhouette of every part, for DXF/2D toolpaths)
│   └─ Labels                   (on a plane at the top of the sheet)
├─ Sheet 2 - 18 mm
└─ Sheet 1 - 12 mm              (each thickness is nested on its own sheets, in its own row)
```

Each sheet component has its origin at the sheet's lower-left corner, with X along the long side. That makes a convenient WCS for a CAM setup.

## How it works

- **Lay flat:** Sheet Nester looks for the direction shared by the largest total area of parallel planar faces. That direction is the thickness, and its two outermost faces are the top and bottom. Bodies with no parallel faces fall back to the thinnest side of their minimum bounding box.
- **Outline:** it uses Fusion's projected body outline (the true silhouette seen from above). Chamfers, bevels, miters and round-overs therefore count with the outline of the part laid flat, and through-holes stay open. If that call fails, it falls back to projecting a mesh of the body.
- **Alignment:** each part is first rotated so its smallest bounding rectangle is axis-aligned, with the long side along X. Panels that are rotated in the model still pack well.
- **Packing:** rectangular mode uses MaxRects packing with several sort orders and rules, plus randomised restarts. True-shape mode uses bottom-left fill on a conservative bitmap of each outline, grown by half the spacing. Two parts can never come closer than the tool diameter, at the cost of up to about one grid cell (≈1 mm) of extra gap.
- Parts that don't fit on one sheet go onto more sheets. Parts larger than the sheet are listed in the summary instead of being placed.

The nesting engine (`SheetNester/nestcore`) is plain Python with no Fusion dependency.

## Tests

```bash
python -m unittest discover tests
```

`tests/test_nestcore.py` checks every layout: parts stay within the margins, parts are never closer than the spacing, multiple sheets work, oversize parts are handled, rotation locks hold, trapezoids interlock, and a small part can be placed inside a ring. `tests/test_lay_flat.py` uses a fake Fusion API to check that a tilted, chamfered panel lands flat with its large face down, at the position the nester reported.

`python tools/make_icons.py` regenerates the toolbar icons.

Developer and AI-agent notes (code layout, conventions, Fusion API pitfalls, how to check API usage without Fusion) are in [AGENTS.md](AGENTS.md).

## Limitations and notes

- The layout is a snapshot. Run the command again after changing the model; with **Replace previous layout** on, the old layout is removed.
- Each body counts as one part. For several identical panels, model them as separate bodies or occurrences, or use **Copies**.
- In true-shape mode a small part can end up inside a larger part's cut-out. Cut inner parts before the outer part's contour.
- Grain direction isn't read from the model. **Lengthwise only** assumes the grain runs along each part's longest side.
