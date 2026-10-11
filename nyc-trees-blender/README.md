# NYC street trees in 3D with Blender

Workshop materials: turn NYC Parks' street-tree inventory into a 3D scene in Blender.
One script imports about 24,000 Midtown Manhattan street trees, makes one coloured object
per genus (same colours as the [NYC Tree Genus Map](https://nyctreegenusmap.com/)), and
lays Google satellite imagery underneath: sharp under the trees, then the surrounding area,
then all five boroughs out to the horizon.

## What's in this repository

| File | What it is |
|---|---|
| `nyc_trees_3d.py` | The script you run inside Blender. Nothing to edit. |
| `data/forestry_tree_points_midtown.geojson` | 31,061 tree records for Midtown Manhattan (7.9 MB), from NYC Parks' *Forestry Tree Points*. |

## Before the workshop

1. **Install Blender** from [blender.org](https://www.blender.org/download/). The script was tested with Blender 5.0.
2. **Optional, for the satellite basemap and 3D buildings:**
   - A Google Maps Platform API key with the **Map Tiles API** enabled. Create it in the [Google Cloud Console](https://console.cloud.google.com/google/maps-apis/) (a billing account is required; Google gives a monthly free allowance).
   - The **Blosm** add-on ([free base version](https://prochitecture.gumroad.com/l/blender-osm)), with your key pasted into *Edit > Preferences > Add-ons > Blosm*. The script reads the key from there, so it never has to be typed into the script.
   - Without a key, the trees still import; only the satellite basemap is skipped.

> **Never commit your API key** to GitHub. If you prefer to paste it into `GOOGLE_API_KEY` at the top of the script, keep that copy on your own computer.

## Run it

1. On this page, click **Code > Download ZIP** and unzip it. Keep `nyc_trees_3d.py` and the `data` folder together.
2. Open Blender and choose **File > New > General**.
3. Click the **Scripting** tab at the top. In the text editor, choose **Text > Open**, select `nyc_trees_3d.py`, then click **Run Script** (the play button).
4. Wait a few minutes. Blender looks frozen while it downloads the imagery; this is normal. A **Finished** message appears at the end.
5. Click the **Layout** tab. The view is already set up (Material Preview, long view distance, zoomed to the trees).

## Explore

- **Navigate:** scroll to zoom, middle-drag to orbit, **Shift + `** (backtick) to fly through the streets (W/A/S/D, Esc to stop).
- **Show or hide a genus:** click the eye icons under **NYC Trees** in the Outliner.
- **3D buildings:** import Google 3D Tiles with Blosm over the same area, then run the script again; trees and basemap re-align to the 3D city and the trees are set on its ground.
- **Floor grid in the way?** Overlays dropdown (two circles, top right of the 3D view) > untick *Floor*.
- Running the script again replaces its own trees and basemap; it never duplicates them.

## Use your own neighbourhood

1. Download *Forestry Tree Points* as GeoJSON from [NYC Open Data](https://data.cityofnewyork.us/Environment/Forestry-Tree-Points/hn5i-inap). The full city file is about 1 GB: too big for Blender in one go, and too big for GitHub.
2. Clip a small area with [GDAL](https://gdal.org/) (`ogr2ogr`; west, south, east, north in degrees):

   ```
   ogr2ogr -f GeoJSON data/my_area.geojson Forestry_Tree_Points.geojson -spat -73.99 40.72 -73.97 40.74
   ```
3. Put the clip in the `data` folder and remove the Midtown file (the script uses the first `.geojson` it finds there), or put the full path in `GEOJSON_PATH` at the top of the script.

## Troubleshooting

| Message or symptom | What it means |
|---|---|
| *Could not find the tree data* | The script and the `data` folder were separated. Re-download the ZIP and open the script from the unzipped folder. |
| *No Google API key found* | Blosm isn't installed or has no key. Trees still import; add the key for the basemap. |
| *Google refused the satellite session (HTTP 403)* | The key doesn't have the **Map Tiles API** enabled, or billing isn't set up. |
| Nothing visible | Hover over the 3D view and press **Home**. |
| Colours look orange | Everything is selected; press **Alt/Option + A** over the 3D view. |

## How it works (short version)

- Trees with structure *Stump*, *Shaft* or *Retired*, or condition *Dead*, are skipped.
- Genus comes from `genusspecies` ("Quercus palustris - pin oak" becomes Oak). The 18 genera of the NYC Tree Genus Map get their legend colour; everything else is "Other".
- Tree height and crown size are **estimated from trunk diameter (DBH)**, about 8.6 m tall for a 12-inch trunk. The inventory has no measured heights, so individual trees can be off by 30 to 50%.
- With Blosm, placement uses Blosm's own coordinate frame and each tree is set on the 3D-tiles ground. Without it, the script georeferences the scene in Web Mercator (EPSG:3857, as BlenderGIS does).

## Credits and licences

- **Tree data:** NYC Department of Parks & Recreation, *Forestry Tree Points*, published by the City of New York on [NYC Open Data](https://data.cityofnewyork.us/Environment/Forestry-Tree-Points/hn5i-inap) (public). The copy here keeps the columns the script uses: `objectid`, `genusspecies`, `dbh`, `tpstructure`, `tpcondition`, `planteddate`.
- **Genus colours:** from the [NYC Tree Genus Map](https://nyctreegenusmap.com/) ([paper](https://www.nature.com/articles/s41597-026-08104-3)).
- **Satellite imagery:** (c) Google, via the Google Maps Platform [Map Tiles API](https://developers.google.com/maps/documentation/tile/satellite). Downloaded fresh on each run and not cached, per Google's [policies](https://developers.google.com/maps/documentation/tile/policies). Credit Google in any image you share.
- **Code:** MIT licence (see `LICENSE`).
