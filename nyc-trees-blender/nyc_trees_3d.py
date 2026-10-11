"""
NYC trees in 3D: one script, nothing to edit.

Open in Blender's Scripting tab and press Run Script (or use the Python Console
one-liner). It does two things in your current scene:
  1. Imports the trees from data/forestry_tree_points_midtown.geojson (found
     automatically next to this script), one coloured object per genus, lined up with Blosm's Google 3D Tiles
     (set on the 3D ground) or with BlenderGIS, whichever the scene uses.
  2. Lays Google satellite imagery on the ground: sharp under the trees,
     then 5 km around them, then all of NYC to the horizon. Uses Google's
     official Map Tiles API with the key from your Blosm add-on preferences.
     Imagery (c) Google; tiles are not cached on disk (Google's terms).

Running it again replaces its own trees and basemap (no duplicates).
Switch the 3D view to Material Preview to see colours and imagery.
Tested with Blender 5.0.1 (bpy).
"""
import bpy, json, math, os
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

# ---------------- settings ----------------
GEOJSON_PATH = ""   # leave empty: uses data/forestry_tree_points_midtown.geojson next to this script
ALIGN_TO = "auto"         # "auto" (Blosm if present, else BlenderGIS), "blosm", "blendergis" or "none"
GENUS_FIELD = None        # None = auto: Genus_Merged / genusspecies / first field containing "genus"
Z_OFFSET = 0.0            # extra raise/lower for every tree (metres)
MAX_FILE_MB = 400         # refuse bigger files (clip first); None disables the check
COLLECTION_NAME = "NYC Trees"
REPLACE_EXISTING = True   # delete trees from a previous run before importing

# ground snapping (Blosm mode)
SNAP_TO_GROUND = True     # sit each tree on the 3D tiles / terrain surface
GROUND_OBJECTS = None     # None = auto (objects named like "Google 3D Tiles"/"Terrain"), or a list of names
ALIGN_BLENDERGIS_BASEMAPS = True  # move BlenderGIS "EXPORT_..." basemaps into the Blosm frame (once)

# point data (Forestry Tree Points)
SKIP_STUMPS_AND_DEAD = True   # keep only tpstructure "Full" and not tpcondition "Dead"
DEFAULT_DBH_IN = 8.0      # used when DBH is missing or 0 (inches)
CROWN_SIDES = 8           # roundness of point-tree crowns (6-12)
ADD_TRUNKS = True         # brown trunk under each point tree

# polygon data (genus crown polygons)
HEIGHT_FIELDS_FT = ["height_max_ft", "TNC_Height"]  # lidar heights in FEET, tried in order
CANOPY_BASE = 0.35        # crown starts at this fraction of tree height
MIN_AREA_M2 = 1.0         # skip crown slivers smaller than this
FIXED_HEIGHT_M = None     # e.g. 12.0 to force one height (metres) for every tree
# ------------------------------------------


def script_folder():
    """Folder of this script when opened in Blender's Text Editor (Text > Open)."""
    for t in bpy.data.texts:
        fp = bpy.path.abspath(t.filepath) if t.filepath else ""
        if fp and os.path.basename(fp).startswith("nyc_trees_3d") and os.path.isfile(fp):
            return os.path.dirname(fp)
    f = globals().get("__file__", "")
    return os.path.dirname(f) if f and os.path.isfile(f) else None


def find_geojson():
    """GEOJSON_PATH if set, else the GeoJSON in the data folder next to this script."""
    if GEOJSON_PATH and GEOJSON_PATH.strip():
        return os.path.expanduser(GEOJSON_PATH.strip())
    tried = []
    folder = script_folder()
    if folder:
        for cand in (os.path.join(folder, "data", "forestry_tree_points_midtown.geojson"),
                     os.path.join(folder, "forestry_tree_points_midtown.geojson")):
            tried.append(cand)
            if os.path.isfile(cand):
                return cand
        data_dir = os.path.join(folder, "data")
        if os.path.isdir(data_dir):
            for n in sorted(os.listdir(data_dir)):
                if n.lower().endswith(".geojson"):
                    return os.path.join(data_dir, n)
    raise FileNotFoundError(
        "Could not find the tree data. Keep nyc_trees_3d.py and the 'data' folder together (as downloaded "
        "from GitHub) and open the script with Text > Open, or put the full path of your GeoJSON in "
        "GEOJSON_PATH at the top of the script." + (f" Looked in: {tried}" if tried else ""))

LEGEND = {  # Latin genus -> (common name, hex color) matching the web map
    "Quercus": ("Oak", "#B22222"), "Acer": ("Maple", "#8DB33A"),
    "Prunus": ("Cherry", "#A84CB8"), "Platanus": ("London plane", "#E6B030"),
    "Pyrus": ("Pear", "#6A3DB8"), "Gleditsia": ("Honey locust", "#3A9488"),
    "Tilia": ("Linden", "#2E6B2A"), "Fraxinus": ("Ash", "#F07060"),
    "Liquidambar": ("Sweetgum", "#5CCB85"), "Ulmus": ("Elm", "#B79CF5"),
    "Zelkova": ("Zelkova", "#CFE0F7"), "Styphnolobium": ("Pagoda tree", "#4A8CF0"),
    "Ginkgo": ("Ginkgo", "#B8641E"), "Robinia": ("Black locust", "#9CCDE8"),
    "Malus": ("Apple", "#E08AB0"), "Ailanthus": ("Tree of heaven", "#C4A07A"),
    "Liriodendron": ("Tulip tree", "#2A6496"), "Betula": ("Birch", "#F2D670"),
}
OTHER = ("Other", "#999999")
TRUNK_COLOR = "#5A4030"
COMMON_TO_LATIN = {v[0].lower(): k for k, v in LEGEND.items()}
GENUS_FIELD_PREFS = ["genus_merged", "genusspecies", "genus_species", "genus"]
FT_TO_M = 0.3048
IN_TO_M = 0.0254
R_EARTH = 6378137.0
WGS84_RADII_SQ = (6378137.0 ** 2, 6378137.0 ** 2, 6356752.3142451793 ** 2)


# ---------- small helpers ----------
def srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_rgba(h):
    h = h.lstrip("#")
    return tuple(srgb_to_linear(int(h[i:i + 2], 16) / 255) for i in (0, 2, 4)) + (1.0,)


def normalize_genus(val):
    """'Quercus palustris - pin oak' / 'quercus' / 'Oak' -> 'Quercus'; else 'Other'."""
    if val is None:
        return "Other"
    s = str(val).strip()
    if not s:
        return "Other"
    first = s.split()[0].capitalize()
    if first in LEGEND:
        return first
    common = s.split(" - ")[-1].strip().lower()
    return COMMON_TO_LATIN.get(s.lower(), COMMON_TO_LATIN.get(common, "Other"))


def to_float(v):
    """Number from int/float/str; None for missing, NA, NaN, <= 0."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and f > 0 else None


def prop(props, name):
    """Case-insensitive property lookup (GenusSpecies vs genusspecies)."""
    if name in props:
        return props[name]
    low = name.lower()
    for k, v in props.items():
        if k.lower() == low:
            return v
    return None


def median(vals):
    v = sorted(vals)
    n = len(v)
    if not n:
        return None
    return v[n // 2] if n % 2 else 0.5 * (v[n // 2 - 1] + v[n // 2])


def tree_size_from_dbh(dbh_in):
    """Rough urban-tree allometry from trunk diameter (inches) -> metres.
    Typical NYC street tree: DBH 12 in -> ~8.6 m tall, ~2.9 m crown radius."""
    d = min(max(dbh_in, 1.0), 60.0)
    return min(4.0 + 0.38 * d, 28.0), min(1.0 + 0.16 * d, 9.0)


# ---------- geometry access ----------
def exterior_rings(geom):
    """Outer rings only; holes are ignored (fine for tree crowns)."""
    if not geom:
        return []
    t, c = geom.get("type"), geom.get("coordinates") or []
    if t == "Polygon":
        return [c[0]] if c else []
    if t == "MultiPolygon":
        return [p[0] for p in c if p]
    return []


def points_of(geom):
    if not geom:
        return []
    t, c = geom.get("type"), geom.get("coordinates")
    if t == "Point" and c:
        return [c]
    if t == "MultiPoint" and c:
        return [p for p in c if p]
    return []


def first_xy(feats):
    for ft in feats:
        g = ft.get("geometry")
        for r in exterior_rings(g):
            if r:
                return r[0][0], r[0][1]
        for p in points_of(g):
            return p[0], p[1]
    return None


def walk_coords(geom, fn):
    """Apply fn(coord_list) to every coordinate of a geometry, in place."""
    def walk(c):
        if c and isinstance(c[0], (int, float)):
            fn(c)
        else:
            for x in c:
                walk(x)
    if geom and geom.get("coordinates"):
        walk(geom["coordinates"])


def mercator_from_lonlat(lon, lat):
    lat = max(min(lat, 85.05), -85.05)
    return (math.radians(lon) * R_EARTH,
            math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * R_EARTH)


def lonlat_from_mercator(x, y):
    return (math.degrees(x / R_EARTH),
            math.degrees(2 * math.atan(math.exp(y / R_EARTH)) - math.pi / 2))


def geojson_epsg(data):
    """EPSG code from a GDAL-style 'crs' member, or None."""
    try:
        name = data["crs"]["properties"]["name"]
    except (KeyError, TypeError):
        return None
    tail = name.replace("::", ":").split(":")[-1]
    return "4326" if tail == "CRS84" else tail


# ---------- Blosm frame (copied from Blosm's threed_tiles code) ----------
def ecef_from_geographic(lat, lon, height=0.0):
    """WGS84 lat/lon/height -> earth-centred XYZ (same maths as Blosm/Cesium)."""
    lat, lon = math.radians(lat), math.radians(lon)
    n = Vector((math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat)))
    n.normalize()
    k = Vector((WGS84_RADII_SQ[0] * n[0], WGS84_RADII_SQ[1] * n[1], WGS84_RADII_SQ[2] * n[2]))
    k /= math.sqrt(n.dot(k))
    return k + n * height


class BlosmFrame:
    """Blender coords of Blosm's Google 3D Tiles: R @ (ECEF - ECEF(origin))."""
    def __init__(self, lat0, lon0):
        self.c = ecef_from_geographic(lat0, lon0, 0.0)
        latc = math.atan(self.c[2] / math.sqrt(self.c[0] ** 2 + self.c[1] ** 2))
        self.r = (Matrix.Rotation(latc - math.pi / 2, 3, "X") @
                  Matrix.Rotation(math.radians(-90.0 - lon0), 3, "Z"))

    def to_scene(self, lon, lat, height=0.0):
        return self.r @ (ecef_from_geographic(lat, lon, height) - self.c)


# ---------- mesh building ----------
def add_prism(bucket, pts, z0, z1):
    """Closed prism from a counter-clockwise outline."""
    verts, faces = bucket
    n, base = len(pts), len(verts)
    verts.extend((x, y, z0) for x, y in pts)
    verts.extend((x, y, z1) for x, y in pts)
    faces.append([base + n + i for i in range(n)])          # top
    faces.append([base + i for i in reversed(range(n))])     # bottom
    for i in range(n):
        j = (i + 1) % n
        faces.append([base + i, base + j, base + n + j, base + n + i])


def add_crown(bucket, x, y, r, z0, z1, sides):
    """Rounded crown: wide in the middle, narrower at bottom and top."""
    verts, faces = bucket
    base = len(verts)
    for z, f in [(z0, 0.55), (z0 + (z1 - z0) * 0.35, 1.0), (z0 + (z1 - z0) * 0.75, 0.85), (z1, 0.35)]:
        for i in range(sides):
            a = 2 * math.pi * i / sides
            verts.append((x + r * f * math.cos(a), y + r * f * math.sin(a), z))
    faces.append([base + i for i in reversed(range(sides))])            # bottom
    faces.append([base + 3 * sides + i for i in range(sides)])          # top
    for k in range(3):
        lo, hi = base + k * sides, base + (k + 1) * sides
        for i in range(sides):
            j = (i + 1) % sides
            faces.append([lo + i, lo + j, hi + j, hi + i])


def regular_polygon(x, y, r, sides):
    return [(x + r * math.cos(2 * math.pi * i / sides), y + r * math.sin(2 * math.pi * i / sides))
            for i in range(sides)]


def clean_ring(ring):
    """Scene-space ring -> 2D points without closing vertex or consecutive duplicates."""
    pts = []
    for p in ring:
        q = (p[0], p[1])
        if not pts or q != pts[-1]:
            pts.append(q)
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts.pop()
    return pts


def signed_area(ring):
    a = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        a += x1 * y2 - x2 * y1
    return a / 2


def get_material(name, hexcol):
    mname = f"Tree_{name}"
    mat = bpy.data.materials.get(mname) or bpy.data.materials.new(mname)
    rgba = hex_rgba(hexcol)
    mat.diffuse_color = rgba  # used in Solid mode with Color: Material
    if bpy.app.version < (5, 0, 0):
        mat.use_nodes = True  # always on (and deprecated) from Blender 5.0
    if mat.node_tree:
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            bsdf.inputs["Base Color"].default_value = rgba
            bsdf.inputs["Roughness"].default_value = 0.8
    return mat


def clear_collection(coll):
    meshes = set()
    for ob in list(coll.objects):
        if ob.type == "MESH":
            meshes.add(ob.data)
        bpy.data.objects.remove(ob, do_unlink=True)
    for me in meshes:
        if me.users == 0:
            bpy.data.meshes.remove(me)


def pick_genus_field(feats):
    if GENUS_FIELD:
        return GENUS_FIELD
    keys = []
    for ft in feats[:200]:
        for k in (ft.get("properties") or {}):
            if k not in keys:
                keys.append(k)
    lower = {k.lower(): k for k in keys}
    for pref in GENUS_FIELD_PREFS:
        if pref in lower:
            return lower[pref]
    return next((k for k in keys if "genus" in k.lower()), None)


# ---------- ground snapping ----------
def mesh_bvh(me):
    """BVH tree straight from a mesh's triangles (no depsgraph needed)."""
    me.calc_loop_triangles()
    nv, nt = len(me.vertices), len(me.loop_triangles)
    if not nv or not nt:
        return None
    try:
        import numpy as np
        co = np.empty(nv * 3, dtype=np.float32)
        me.vertices.foreach_get("co", co)
        tri = np.empty(nt * 3, dtype=np.int32)
        me.loop_triangles.foreach_get("vertices", tri)
        return BVHTree.FromPolygons(co.reshape(-1, 3).tolist(), tri.reshape(-1, 3).tolist())
    except ImportError:
        return BVHTree.FromPolygons([v.co[:] for v in me.vertices],
                                    [t.vertices[:] for t in me.loop_triangles])


class Ground:
    CELL = 200.0   # metres; spatial index so each ray only tests tiles under it

    def __init__(self, objects):
        self.items = []
        self.grid = {}
        for ob in objects:
            bvh = mesh_bvh(ob.data)        # from mesh data: works for hidden/disabled objects too
            if bvh is None:
                continue
            mw = ob.matrix_world.copy()
            corners = [mw @ Vector(c) for c in ob.bound_box]
            box = (min(c.x for c in corners), max(c.x for c in corners),
                   min(c.y for c in corners), max(c.y for c in corners))
            idx = len(self.items)
            self.items.append((bvh, mw, mw.inverted(), box))
            for gx in range(int(box[0] // self.CELL), int(box[1] // self.CELL) + 1):
                for gy in range(int(box[2] // self.CELL), int(box[3] // self.CELL) + 1):
                    self.grid.setdefault((gx, gy), []).append(idx)

    def hit_z(self, x, y, z_top, z_bottom):
        best = None
        for k in self.grid.get((int(x // self.CELL), int(y // self.CELL)), ()):
            bvh, mw, mwi, box = self.items[k]
            if not (box[0] <= x <= box[1] and box[2] <= y <= box[3]):
                continue
            o = mwi @ Vector((x, y, z_top))
            d = (mwi.to_3x3() @ Vector((0, 0, -1))).normalized()
            loc, _n, _i, _d = bvh.ray_cast(o, d, (z_top - z_bottom) * 2)
            if loc is not None:
                z = (mw @ loc).z
                best = z if best is None else max(best, z)
        return best

    # rays at the tree and on rings 4 m and 8 m around it: the lowest hit is the
    # sidewalk/street, not the tree canopy blobs, awnings or roofs in Google's mesh
    PROBES = [(0.0, 0.0)] + [(r * math.cos(a * math.pi / 4), r * math.sin(a * math.pi / 4))
                             for r in (4.0, 8.0) for a in range(8)]

    def ground_z(self, x, y, z_guess):
        hits = []
        for dx, dy in self.PROBES:
            z = self.hit_z(x + dx, y + dy, z_guess + 1500.0, z_guess - 1500.0)
            if z is not None:
                hits.append(z)
        return min(hits) if hits else None


def find_ground_objects(tree_coll):
    tree_obs = set(tree_coll.all_objects) if tree_coll else set()
    if GROUND_OBJECTS:
        obs = [bpy.context.scene.objects[n] for n in GROUND_OBJECTS if n in bpy.context.scene.objects]
    else:
        keys = ("3d tiles", "3dtiles", "google 3d", "terrain")
        obs = []
        for ob in bpy.context.scene.objects:      # only objects in this scene
            if ob.type != "MESH" or ob in tree_obs or ob.name.startswith("EXPORT_"):
                continue
            names = [ob.name.lower()] + [c.name.lower() for c in ob.users_collection]
            if any(k in n for n in names for k in keys):
                obs.append(ob)
    return [ob for ob in obs if ob.type == "MESH" and len(ob.data.polygons) > 0]


def fit_ground_plane(pts):
    """Least-squares plane z = a + b*x + c*y through (x, y, z) points (x, y relative
    to the tree), refitted up to 8 times without points > 1 m above it (canopy, roofs).
    Returns a = plane height at the tree, or None."""
    for _ in range(8):
        n = len(pts)
        if n < 6:
            return None
        sx = sum(p[0] for p in pts); sy = sum(p[1] for p in pts); sz = sum(p[2] for p in pts)
        sxx = sum(p[0] * p[0] for p in pts); syy = sum(p[1] * p[1] for p in pts)
        sxy = sum(p[0] * p[1] for p in pts)
        sxz = sum(p[0] * p[2] for p in pts); syz = sum(p[1] * p[2] for p in pts)
        lam = 25.0 * n              # mild ridge: trees along one street can't tilt the plane wildly
        m = Matrix(((n, sx, sy), (sx, sxx + lam, sxy), (sy, sxy, syy + lam)))
        try:
            a, b, c = m.inverted() @ Vector((sz, sxz, syz))
        except ValueError:
            a, b, c = sz / n, 0.0, 0.0
        if abs(b) > 0.15 or abs(c) > 0.15:   # steeper than any NYC street: refit flat
            b = max(-0.15, min(0.15, b)); c = max(-0.15, min(0.15, c))
            a = (sz - b * sx - c * sy) / n
        keep = [p for p in pts if p[2] - (a + b * p[0] + c * p[1]) <= 1.0]
        if len(keep) == n:
            break
        pts = keep
    return a


def snap_bases(trees, ground, radius=100.0):
    """Sit each tree on the ground: lowest of 17 rays (tree + rings at 4 m / 8 m),
    then checked against a canopy-resistant plane through nearby trees' ground hits,
    so roofs, awnings, canopy blobs or holes in the 3D mesh cannot lift or sink it."""
    from mathutils.kdtree import KDTree
    raw = []
    for i, t in enumerate(trees):
        raw.append(ground.ground_z(t["x"], t["y"], t["z"]))
        if i and i % 5000 == 0:
            print(f"  ground: {i}/{len(trees)} trees")
    good = [(t["x"], t["y"], g - t["z"]) for g, t in zip(raw, trees) if g is not None]
    if not good:
        return 0, None, 0
    glob = median([o for _x, _y, o in good])
    kd = KDTree(len(good))
    for i, (x, y, _o) in enumerate(good):
        kd.insert((x, y, 0.0), i)
    kd.balance()

    def local_ref(t):
        """(ground offset expected at the tree, tolerance up, tolerance down)."""
        near = kd.find_range((t["x"], t["y"], 0.0), radius)
        if len(near) >= 10:
            if len(near) > 400:          # keep it fast in dense areas
                near = sorted(near, key=lambda e: e[2])[:400]
            pts = [(good[i][0] - t["x"], good[i][1] - t["y"], good[i][2]) for _co, i, _d in near]
            a = fit_ground_plane(pts)
            if a is not None:
                return a, 4.0, 4.0
        if len(near) >= 3:               # sparse: compare with the nearby trees' median
            return median([good[i][2] for _co, i, _d in near]), 5.0, 6.0
        near = kd.find_range((t["x"], t["y"], 0.0), radius * 3)
        if len(near) >= 3:
            return median([good[i][2] for _co, i, _d in near]), 8.0, 10.0
        return glob, 12.0, 12.0

    fixed = 0
    for g, t in zip(raw, trees):
        ref, up, down = local_ref(t)
        off = None if g is None else g - t["z"]
        if off is None or off > ref + up or off < ref - down:
            t["z"] = t["z"] + ref
            fixed += 1
        else:
            t["z"] = g
    return len(good), glob, fixed


def align_basemaps_to_blosm(scn, frame, z_shift):
    """Move BlenderGIS 'EXPORT_*' meshes from Web Mercator into the Blosm frame (once)."""
    if not ("crs x" in scn and "crs y" in scn):
        return []
    ox, oy = float(scn["crs x"]), float(scn["crs y"])
    s = float(scn.get("scale", 1) or 1)
    moved = []
    for ob in scn.objects:
        if ob.type != "MESH" or not ob.name.startswith("EXPORT_") or ob.get("aligned_to_blosm"):
            continue
        backup = ob.data.copy()
        backup.name = ob.data.name + "_BlenderGIS_original"
        backup.use_fake_user = True
        mw = ob.matrix_world.copy()
        mwi = ob.matrix_world.inverted()
        for v in ob.data.vertices:
            w = mw @ v.co
            lon, lat = lonlat_from_mercator(w.x * s + ox, w.y * s + oy)
            p = frame.to_scene(lon, lat)
            p.z += z_shift
            v.co = mwi @ p
        ob.data.update()
        ob["aligned_to_blosm"] = True
        moved.append(ob.name)
    return moved


# ---------- main ----------
def trees_main():
    geojson_path = find_geojson()
    if not os.path.isfile(geojson_path):
        raise FileNotFoundError(f"GeoJSON not found: {geojson_path}")
    print(f"Tree data: {geojson_path}")
    size_mb = os.path.getsize(geojson_path) / 1e6
    if MAX_FILE_MB and size_mb > MAX_FILE_MB:
        raise RuntimeError(f"File is {size_mb:.0f} MB. Clip it to a smaller area first "
                           f"(or raise MAX_FILE_MB if you have lots of RAM).")

    with open(geojson_path) as f:
        data = json.load(f)
    feats = data.get("features", [])
    if not feats:
        raise RuntimeError("No features found in the GeoJSON (is the clip area empty?).")
    fxy = first_xy(feats)
    if fxy is None:
        raise RuntimeError("No Point or Polygon geometries in the file.")

    epsg = geojson_epsg(data)
    if epsg is None:
        epsg = "4326" if abs(fxy[0]) <= 180 and abs(fxy[1]) <= 90 else None
    scn = bpy.context.scene
    srid = scn.get("SRID")

    # ---- choose the frame ----
    mode = ALIGN_TO
    if mode == "auto":
        if "lat" in scn and "lon" in scn:
            mode = "blosm"
        elif "crs x" in scn and "crs y" in scn:
            mode = "blendergis"
        else:
            mode = "none"
    print(f"Aligning to: {mode}")

    # every coordinate -> [x, y, z] in Blender units, in place
    if mode == "blosm":
        if epsg not in ("4326", "3857", "900913"):
            raise RuntimeError(f"Blosm mode needs lon/lat or EPSG:3857 input, file is EPSG:{epsg}.")
        frame = BlosmFrame(float(scn["lat"]), float(scn["lon"]))
        print(f"Blosm origin: lat {float(scn['lat']):.6f}, lon {float(scn['lon']):.6f}")

        def conv(c):
            lon, lat = (c[0], c[1]) if epsg == "4326" else lonlat_from_mercator(c[0], c[1])
            p = frame.to_scene(lon, lat)
            c[:] = [p.x, p.y, p.z]
        m = 1.0
    else:
        if epsg == "4326":
            if mode == "blendergis" and srid and "3857" not in str(srid):
                raise RuntimeError(f"File is lon/lat but the scene CRS is {srid}. Reproject the file "
                                   f"to that CRS first (ogr2ogr ... -t_srs {srid}).")
            print("File is lon/lat: converting to Web Mercator (EPSG:3857).")
        elif mode == "blendergis" and epsg and srid and epsg not in str(srid):
            print(f"WARNING: file is EPSG:{epsg} but the scene is {srid}; trees will be misplaced.")
        if epsg == "4326":
            fm = mercator_from_lonlat(*fxy)
        else:
            fm = fxy
        if mode == "blendergis":
            ox, oy = float(scn["crs x"]), float(scn["crs y"])
            s = float(scn.get("scale", 1) or 1)
            print(f"BlenderGIS origin: {ox:.1f}, {oy:.1f}  scene CRS: {srid}  scale: {s}")
        else:
            ox, oy, s = fm[0], fm[1], 1.0
            if epsg in ("4326", "3857", "900913"):
                # georeference the scene (BlenderGIS-style keys) so the basemap and later runs line up
                scn["SRID"] = "EPSG:3857"
                scn["crs x"], scn["crs y"] = float(ox), float(oy)
                print("Scene had no georeference: set its origin (EPSG:3857) at the trees.")
            else:
                print("WARNING: scene has no Blosm or BlenderGIS georeference; centring on the data.")
        is_merc = epsg in ("4326", "3857", "900913")
        if is_merc:
            lat = lonlat_from_mercator(*fm)[1]
            k = 1 / math.cos(math.radians(lat))
            print(f"Web Mercator at lat {lat:.3f}: scale factor {k:.3f} applied to tree sizes")
        else:
            k = 1.0
        m = k / s

        def conv(c):
            x, y = mercator_from_lonlat(c[0], c[1]) if epsg == "4326" else (c[0], c[1])
            c[:] = [(x - ox) / s, (y - oy) / s, 0.0]

    for ft in feats:
        walk_coords(ft.get("geometry"), conv)

    gfield = pick_genus_field(feats)
    print("Fields:", list((feats[0].get("properties") or {}).keys()))
    print("Genus field:", gfield)
    if gfield is None:
        print("WARNING: no genus field found; every tree will be 'Other'. Set GENUS_FIELD.")

    # ---- collect trees ----
    trees = []
    stats = {"points": 0, "polygons": 0, "lidar_h": 0, "est_h": 0,
             "skipped_status": 0, "skipped_geom": 0, "dbh_default": 0}
    for ft in feats:
        props = ft.get("properties") or {}
        geom = ft.get("geometry")
        latin = normalize_genus(props.get(gfield)) if gfield else "Other"

        pts = points_of(geom)
        if pts:
            if SKIP_STUMPS_AND_DEAD:
                structure = str(prop(props, "tpstructure") or "Full").strip().lower()
                condition = str(prop(props, "tpcondition") or "").strip().lower()
                if structure not in ("full", "") or condition == "dead":
                    stats["skipped_status"] += 1
                    continue
            dbh = to_float(prop(props, "dbh"))
            if dbh is None:
                dbh = DEFAULT_DBH_IN
                stats["dbh_default"] += 1
            h_real, r_real = tree_size_from_dbh(dbh)
            if FIXED_HEIGHT_M:
                h_real = FIXED_HEIGHT_M
            for p in pts:
                trees.append({"kind": "point", "genus": latin, "x": p[0], "y": p[1], "z": p[2],
                              "h": h_real * m, "r": r_real * m,
                              "trunk_r": max(dbh * IN_TO_M / 2, 0.08) * m})
                stats["points"] += 1
                stats["est_h"] += 1
            continue

        rings = exterior_rings(geom)
        if not rings:
            stats["skipped_geom"] += 1
            continue
        if FIXED_HEIGHT_M:
            h_ft = FIXED_HEIGHT_M / FT_TO_M
        else:
            h_ft = next((to_float(prop(props, f)) for f in HEIGHT_FIELDS_FT
                         if to_float(prop(props, f))), None)
        for ring in rings:
            zs = [c[2] for c in ring]
            pts2 = clean_ring(ring)
            if len(pts2) < 3:
                stats["skipped_geom"] += 1
                continue
            area = signed_area(pts2)
            real_area = abs(area) / (m * m)
            if real_area < MIN_AREA_M2:
                stats["skipped_geom"] += 1
                continue
            if area < 0:
                pts2.reverse()
            if h_ft:
                h_real = h_ft * FT_TO_M
                stats["lidar_h"] += 1
            else:
                h_real = max(4.0, min(30.0, 2.5 * math.sqrt(real_area / math.pi)))
                stats["est_h"] += 1
            cx = sum(p[0] for p in pts2) / len(pts2)
            cy = sum(p[1] for p in pts2) / len(pts2)
            trees.append({"kind": "poly", "genus": latin, "pts": pts2, "x": cx, "y": cy,
                          "z": sum(zs) / len(zs), "h": h_real * m})
            stats["polygons"] += 1

    if not trees:
        raise RuntimeError(f"Nothing to import. Stats: {stats}")

    coll = bpy.data.collections.get(COLLECTION_NAME)
    if not coll:
        coll = bpy.data.collections.new(COLLECTION_NAME)
        scn.collection.children.link(coll)
    elif REPLACE_EXISTING:
        clear_collection(coll)

    # ---- set trees on the ground (Blosm) ----
    z_shift = 0.0
    if mode == "blosm" and SNAP_TO_GROUND:
        ground_obs = find_ground_objects(coll)
        if ground_obs:
            print("Ground surface:", ", ".join(o.name for o in ground_obs))
            res = snap_bases(trees, Ground(ground_obs))
            if res[0]:
                n_hit, z_shift, fixed = res
                print(f"  {n_hit}/{len(trees)} trees found ground; median ground {z_shift:+.1f} m "
                      f"from ellipsoid; {fixed} adjusted (no hit, or rooftop/awning hit)")
            else:
                print("  WARNING: no tree is above the ground objects; trees left at ellipsoid height. "
                      "Import 3D tiles covering the same area, or set Z_OFFSET.")
        else:
            print("No 3D tiles / terrain object found; trees left at ellipsoid height (z ~ 0).")
        if ALIGN_BLENDERGIS_BASEMAPS:
            moved = align_basemaps_to_blosm(scn, frame, z_shift - 1.0)
            if moved:
                print("Moved BlenderGIS basemaps into the Blosm frame:", ", ".join(moved),
                      "(original meshes kept as *_BlenderGIS_original)")

    # ---- build meshes ----
    buckets, counts = {}, {}
    trunks = ([], [])
    for t in trees:
        bucket = buckets.setdefault(t["genus"], ([], []))
        base = t["z"] + Z_OFFSET
        if t["kind"] == "point":
            h = t["h"]
            add_crown(bucket, t["x"], t["y"], t["r"], base + h * 0.3, base + h, CROWN_SIDES)
            if ADD_TRUNKS:
                add_prism(trunks, regular_polygon(t["x"], t["y"], t["trunk_r"], 5),
                          base, base + h * 0.3 + 0.05 * m)
        else:
            add_prism(bucket, t["pts"], base + t["h"] * CANOPY_BASE, base + t["h"])
        counts[t["genus"]] = counts.get(t["genus"], 0) + 1

    def make_object(name, bucket, mat):
        verts, faces = bucket
        me = bpy.data.meshes.new(name)
        me.from_pydata(verts, [], faces)
        me.validate()
        me.update()
        ob = bpy.data.objects.new(name, me)
        ob.data.materials.append(mat)
        coll.objects.link(ob)
        return ob

    for latin in sorted(buckets, key=lambda g: -counts[g]):
        name, hexcol = LEGEND.get(latin, OTHER)
        ob = make_object(f"Trees_{name}", buckets[latin], get_material(name, hexcol))
        ob["genus"] = latin
        print(f"  {name:16s} {counts[latin]:>7} trees")
    if trunks[0]:
        make_object("Trees_Trunks", trunks, get_material("Trunk", TRUNK_COLOR))

    print(f"Done: {len(trees)} trees ({stats['points']} points, "
          f"{stats['polygons']} crown polygons) from {len(feats)} features.")
    print(f"  heights: {stats['lidar_h']} from lidar, {stats['est_h']} estimated "
          f"(points: from DBH; {stats['dbh_default']} had no DBH)")
    print(f"  skipped: {stats['skipped_status']} stumps/dead/shafts, "
          f"{stats['skipped_geom']} empty or tiny geometries")


# ================= sharp basemap =================
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from mathutils import Matrix, Vector

# ---------------- basemap settings ----------------
GOOGLE_API_KEY = ""       # leave empty: read automatically from the Blosm add-on preferences
ZOOM = 19                 # 19 = ~0.23 m/pixel in NYC, 18 = ~0.45 m; lowered automatically for big areas
MAX_TILES = 2500          # per layer; if the area needs more tiles, zoom is lowered automatically
AREA = "trees"            # "trees" = area covered by the NYC Trees collection (or the GeoJSON)
PADDING_M = 60            # extra margin around the trees (metres)
CHUNK_TILES = 8           # 8 x 8 tiles = 2048 px textures (keeps the GPU happy)
GRID_RES = 8              # mesh subdivisions per chunk (keeps it exact in the Blosm frame)
BASEMAP_Z_OFFSET = -0.3   # metres relative to the trees' ground (slightly below avoids flicker)
HIDE_OLD_BASEMAPS = True  # hide BlenderGIS "EXPORT_..." basemaps
BASEMAP_COLLECTION = "NYC Basemap - Google satellite"
OLD_BASEMAP_COLLECTIONS = ("NYC Basemap", "NYC Basemap - map", "NYC Basemap - aerial photo")
GOOGLE_TILES_BASE = "https://tile.googleapis.com"
# ------------------------------------------

R_EARTH = 6378137.0
WGS84_A = 6378137.0
WGS84_B = 6356752.3142451793
WGS84_RADII_SQ = (WGS84_A ** 2, WGS84_A ** 2, WGS84_B ** 2)
USER_AGENT = "nyc-trees-blender-basemap/1.0"


# ---------- projections ----------
def lonlat_from_mercator(x, y):
    return math.degrees(x / R_EARTH), math.degrees(2 * math.atan(math.exp(y / R_EARTH)) - math.pi / 2)


def mercator_from_lonlat(lon, lat):
    lat = max(min(lat, 85.05), -85.05)
    return math.radians(lon) * R_EARTH, math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * R_EARTH


def tile_xy(lon, lat, z):
    """Fractional XYZ tile coordinates."""
    n = 2 ** z
    lat = max(min(lat, 85.05), -85.05)
    x = (lon + 180.0) / 360.0 * n
    y = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n
    return x, y


def lonlat_from_tile(x, y, z):
    n = 2 ** z
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lon, lat


def ecef_from_geographic(lat, lon, height=0.0):
    lat, lon = math.radians(lat), math.radians(lon)
    n = Vector((math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat)))
    n.normalize()
    k = Vector((WGS84_RADII_SQ[0] * n[0], WGS84_RADII_SQ[1] * n[1], WGS84_RADII_SQ[2] * n[2]))
    k /= math.sqrt(n.dot(k))
    return k + n * height


def geographic_from_ecef(p):
    """ECEF -> (lon, lat, height), WGS84 (Bowring, then 3 refinements)."""
    x, y, z = p
    e2 = 1 - (WGS84_B / WGS84_A) ** 2
    lon = math.atan2(y, x)
    r = math.hypot(x, y)
    lat = math.atan2(z, r * (1 - e2))
    for _ in range(5):
        n = WGS84_A / math.sqrt(1 - e2 * math.sin(lat) ** 2)
        h = r / math.cos(lat) - n
        lat = math.atan2(z, r * (1 - e2 * n / (n + h)))
    n = WGS84_A / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    h = r / math.cos(lat) - n
    return math.degrees(lon), math.degrees(lat), h


class BlosmFrame:
    """Same placement as Blosm's Google 3D Tiles (and import_nyc_trees.py)."""
    def __init__(self, lat0, lon0):
        self.c = ecef_from_geographic(lat0, lon0, 0.0)
        latc = math.atan(self.c[2] / math.sqrt(self.c[0] ** 2 + self.c[1] ** 2))
        self.r = (Matrix.Rotation(latc - math.pi / 2, 3, "X") @
                  Matrix.Rotation(math.radians(-90.0 - lon0), 3, "Z"))
        self.ri = self.r.transposed()

    def to_scene(self, lon, lat, height=0.0):
        return self.r @ (ecef_from_geographic(lat, lon, height) - self.c)

    def to_lonlat(self, p):
        lon, lat, _h = geographic_from_ecef(self.ri @ Vector(p) + self.c)
        return lon, lat


class GisFrame:
    """BlenderGIS: Web Mercator relative to scene origin."""
    def __init__(self, ox, oy, s):
        self.ox, self.oy, self.s = ox, oy, s

    def to_scene(self, lon, lat, height=0.0):
        x, y = mercator_from_lonlat(lon, lat)
        return Vector(((x - self.ox) / self.s, (y - self.oy) / self.s, 0.0))

    def to_lonlat(self, p):
        return lonlat_from_mercator(p[0] * self.s + self.ox, p[1] * self.s + self.oy)


# ---------- ground height from the trees ----------
class TreeGround:
    """Ground offset (scene z minus ellipsoid z) interpolated from the tree trunks."""
    def __init__(self, frame):
        from mathutils.kdtree import KDTree
        self.frame = frame
        self.pts = []
        ob = bpy.context.scene.objects.get("Trees_Trunks")
        if ob is not None and isinstance(frame, BlosmFrame):
            mw = ob.matrix_world
            vs = ob.data.vertices
            for t in range(len(vs) // 10):            # 10 verts per trunk, first 5 = bottom ring
                c = sum((mw @ vs[t * 10 + i].co for i in range(5)), Vector()) / 5
                lon, lat = frame.to_lonlat(c)
                self.pts.append((c.x, c.y, c.z - frame.to_scene(lon, lat).z))
        self.kd = None
        if self.pts:
            self.kd = KDTree(len(self.pts))
            for i, (x, y, _o) in enumerate(self.pts):
                self.kd.insert((x, y, 0.0), i)
            self.kd.balance()

    def offset(self, x, y):
        if self.kd is None:
            return 0.0
        near = sorted(self.pts[i][2] for _co, i, _d in self.kd.find_n((x, y, 0.0), 12))
        return near[len(near) // 2]


# ---------- tiles ----------
def find_google_key():
    """The Google Maps Platform key Blosm already uses for Google 3D Tiles."""
    if GOOGLE_API_KEY.strip():
        return GOOGLE_API_KEY.strip()
    for _name, addon in bpy.context.preferences.addons.items():
        prefs = getattr(addon, "preferences", None)
        key = getattr(prefs, "googleMapsApiKey", "") if prefs is not None else ""
        if key and key.strip():
            return key.strip()
    raise RuntimeError("No Google API key found. Open Edit > Preferences > Add-ons > Blosm and check that "
                       "the Google 3D Tiles key is filled in (the same key works for satellite tiles).")


class GoogleSatellite:
    """Google Maps Platform Map Tiles API, 2D satellite tiles (official API, needs the key)."""
    def __init__(self, key):
        import urllib.error
        self.key = key
        body = json.dumps({"mapType": "satellite", "language": "en-US", "region": "US"}).encode()
        req = urllib.request.Request(f"{GOOGLE_TILES_BASE}/v1/createSession?key={key}", data=body,
                                     headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                info = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="ignore")[:300]
            raise RuntimeError(f"Google refused the satellite session (HTTP {e.code}). The key must have the "
                               f"'Map Tiles API' enabled in Google Cloud Console. Google said: {detail}")
        self.session = info["session"]
        print("Google satellite session created.")

    def tile(self, z, x, y, attempts=3):
        """Tile image bytes, or None if Google has no tile there."""
        import urllib.error, time
        url = f"{GOOGLE_TILES_BASE}/v1/2dtiles/{z}/{x}/{y}?session={self.session}&key={self.key}"
        for attempt in range(attempts):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=20) as r:
                    return r.read()
            except urllib.error.HTTPError as e:
                if e.code in (429, 503) and attempt + 1 < attempts:   # throttled: wait briefly, retry
                    time.sleep(0.4 * (attempt + 1))
                    continue
                return None
            except Exception:
                if attempt + 1 < attempts:
                    time.sleep(0.4 * (attempt + 1))
        return None

    def copyright(self, z, box):
        w, s, e, n = box
        url = (f"{GOOGLE_TILES_BASE}/tile/v1/viewport?session={self.session}&key={self.key}"
               f"&zoom={z}&north={n}&south={s}&east={e}&west={w}")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode()).get("copyright", "")
        except Exception:
            return ""


def load_tile_pixels(path, size=256):
    import numpy as np
    img = bpy.data.images.load(path, check_existing=False)
    try:
        w, h = img.size
        px = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(px)
        px = px.reshape(h, w, 4)
        if (w, h) != (size, size):          # rare: resample to 256 by nearest neighbour
            yi = (np.arange(size) * h // size).astype(int)
            xi = (np.arange(size) * w // size).astype(int)
            px = px[yi][:, xi]
        return px
    finally:
        bpy.data.images.remove(img)


def area_from_geojson(path, pad_deg=0.0008):
    """Lon/lat box of a GeoJSON (lon/lat or EPSG:3857 coordinates)."""
    if not os.path.isfile(path):
        raise RuntimeError(f"No 'NYC Trees' collection and GeoJSON not found: {path}. "
                           "Run import_nyc_trees.py first, or set AREA = (west, south, east, north).")
    with open(path) as f:
        data = json.load(f)
    xs, ys = [], []
    def walk(c):
        if c and isinstance(c[0], (int, float)):
            xs.append(c[0]); ys.append(c[1])
        else:
            for x in c:
                walk(x)
    for ft in data.get("features", []):
        g = ft.get("geometry")
        if g and g.get("coordinates"):
            walk(g["coordinates"])
    if not xs:
        raise RuntimeError("GeoJSON has no coordinates.")
    if max(abs(min(xs)), abs(max(xs))) > 180:          # EPSG:3857 metres
        (w, s), (e, n) = lonlat_from_mercator(min(xs), min(ys)), lonlat_from_mercator(max(xs), max(ys))
    else:
        w, s, e, n = min(xs), min(ys), max(xs), max(ys)
    print("Area taken from the GeoJSON (trees not imported yet).")
    return w - pad_deg, s - pad_deg, e + pad_deg, n + pad_deg


# ---------- main ----------
NYC_BBOX = (-74.28, 40.48, -73.68, 40.93)   # all five boroughs
CONTEXT_PAD_M = 5000                         # surrounding layer: this far around the trees


def detect_frame():
    scn = bpy.context.scene
    if "lat" in scn and "lon" in scn:
        print("Aligning to: blosm")
        return BlosmFrame(float(scn["lat"]), float(scn["lon"]))
    if "crs x" in scn and "crs y" in scn:
        srid = str(scn.get("SRID", "EPSG:3857"))
        if "3857" not in srid:
            raise RuntimeError(f"BlenderGIS scene CRS is {srid}; this script supports EPSG:3857 scenes.")
        print("Aligning to: blendergis")
        return GisFrame(float(scn["crs x"]), float(scn["crs y"]), float(scn.get("scale", 1) or 1))
    raise RuntimeError("Scene has no georeference, so the basemap cannot be placed.")


def trees_area(frame):
    """Lon/lat box of the imported trees (or the GeoJSON), plus PADDING_M."""
    if AREA != "trees":
        return AREA
    coll = bpy.data.collections.get("NYC Trees")
    if not coll or not coll.objects:
        return area_from_geojson(find_geojson())
    xs, ys = [], []
    for ob in coll.objects:
        for c in ob.bound_box:
            w = ob.matrix_world @ Vector(c)
            xs.append(w.x); ys.append(w.y)
    k = 1.0 if isinstance(frame, BlosmFrame) else 1 / math.cos(math.radians(
        frame.to_lonlat(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, 0))[1]))
    pad = PADDING_M * k
    corners = [frame.to_lonlat((x, y, 0.0)) for x in (min(xs) - pad, max(xs) + pad)
               for y in (min(ys) - pad, max(ys) + pad)]
    return (min(c[0] for c in corners), min(c[1] for c in corners),
            max(c[0] for c in corners), max(c[1] for c in corners))


def pad_box(box, metres):
    w, s, e, n = box
    dlat = metres / 111320.0
    dlon = metres / (111320.0 * math.cos(math.radians((s + n) / 2)))
    return w - dlon, s - dlat, e + dlon, n + dlat


def tile_range(box, z, max_tiles):
    w, s, e, n = box
    while True:
        x0, y0 = tile_xy(w, n, z)
        x1, y1 = tile_xy(e, s, z)
        tx0, ty0, tx1, ty1 = int(x0), int(y0), int(x1), int(y1)
        n_tiles = (tx1 - tx0 + 1) * (ty1 - ty0 + 1)
        if n_tiles <= max_tiles or z <= 10:
            return z, tx0, ty0, tx1, ty1, n_tiles
        z -= 1


def parent_fill(google, z, x, y, tmp, max_up=4):
    """A missing tile rebuilt from the same spot at a lower zoom (upscaled)."""
    import numpy as np
    for k in range(1, max_up + 1):
        n = 2 ** k
        data = google.tile(z - k, x // n, y // n, attempts=2)
        if not data:
            continue
        path = os.path.join(tmp, f"parent_{z - k}_{x // n}_{y // n}_{x}_{y}.img")
        with open(path, "wb") as f:
            f.write(data)
        try:
            par = load_tile_pixels(path)          # bottom row first
        except Exception:
            continue
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        sz = 256 // n
        ox = (x % n) * sz
        oy_top = (y % n) * sz                     # offset from the top of the parent tile
        block = par[256 - oy_top - sz:256 - oy_top, ox:ox + sz, :]
        return np.repeat(np.repeat(block, n, axis=0), n, axis=1)
    return None


def build_layer(np, coll, frame, ground, google, layer, box, zoom, max_tiles, z_offset, hole=None, grid_res=None):
    import tempfile, shutil, time
    z, tx0, ty0, tx1, ty1, n_tiles = tile_range(box, zoom, max_tiles)
    m_per_px = 2 * math.pi * R_EARTH / (256 * 2 ** z) * math.cos(math.radians((box[1] + box[3]) / 2))
    print(f"Layer '{layer}': zoom {z}, {n_tiles} tiles, {m_per_px:.2f} m per pixel")
    tmp = tempfile.mkdtemp(prefix="nyc_google_")      # tiles live here only while a piece is built
    n_chunks, got_total, done, filled = 0, 0, 0, 0
    try:
        for cy in range(ty0, ty1 + 1, CHUNK_TILES):
            for cx in range(tx0, tx1 + 1, CHUNK_TILES):
                ncx = min(CHUNK_TILES, tx1 + 1 - cx)
                ncy = min(CHUNK_TILES, ty1 + 1 - cy)
                gr = grid_res or GRID_RES
                if hole:                       # piece fully covered by a sharper layer: skip download
                    lo_w, lo_n = lonlat_from_tile(cx, cy, z)
                    lo_e, lo_s = lonlat_from_tile(cx + ncx, cy + ncy, z)
                    if hole[0] <= lo_w and lo_e <= hole[2] and hole[1] <= lo_s and lo_n <= hole[3]:
                        done += ncx * ncy
                        continue
                cells = [(i, j) for j in range(ncy) for i in range(ncx)]

                def get(ij):
                    data = google.tile(z, cx + ij[0], cy + ij[1])
                    if not data:
                        return ij, None
                    path = os.path.join(tmp, f"{z}_{cx + ij[0]}_{cy + ij[1]}.img")
                    with open(path, "wb") as f:
                        f.write(data)
                    return ij, path

                with ThreadPoolExecutor(max_workers=6) as ex:
                    results = list(ex.map(get, cells))
                # second, gentler pass for tiles Google throttled or dropped
                retry = [k for k, (_ij, path) in enumerate(results) if path is None]
                if retry:
                    time.sleep(1.0)
                    with ThreadPoolExecutor(max_workers=2) as ex:
                        for k, res in zip(retry, ex.map(get, [results[k][0] for k in retry])):
                            results[k] = res
                done += len(cells)
                W, H = ncx * 256, ncy * 256
                canvas = np.full((H, W, 4), 0.5, dtype=np.float32)
                canvas[:, :, 3] = 1.0
                got = 0
                for (i, j), path in results:
                    if path is None:                  # still missing: use a lower zoom of the same spot
                        px = parent_fill(google, z, cx + i, cy + j, tmp)
                        if px is None:
                            continue
                        r0 = (ncy - 1 - j) * 256
                        canvas[r0:r0 + 256, i * 256:(i + 1) * 256, :] = px
                        canvas[r0:r0 + 256, i * 256:(i + 1) * 256, 3] = 1.0
                        got += 1
                        filled += 1
                        continue
                    try:
                        px = load_tile_pixels(path)
                    except Exception as e:
                        print(f"  could not read a tile: {e}")
                        continue
                    finally:
                        try:
                            os.remove(path)
                        except OSError:
                            pass
                    r0 = (ncy - 1 - j) * 256          # Blender images start at the bottom row
                    canvas[r0:r0 + 256, i * 256:(i + 1) * 256, :] = px
                    canvas[r0:r0 + 256, i * 256:(i + 1) * 256, 3] = 1.0
                    got += 1
                got_total += got
                print(f"  {done}/{n_tiles} tiles")
                if not got:
                    continue
                name = f"NYC_Basemap_{layer}_{z}_{cx}_{cy}"
                img = bpy.data.images.new(name, W, H, alpha=False)
                img.pixels.foreach_set(canvas.ravel())
                jpg = os.path.join(tmp, name + ".jpg")
                img.filepath_raw = jpg
                img.file_format = "JPEG"
                img.save()
                bpy.data.images.remove(img)
                img = bpy.data.images.load(jpg, check_existing=False)
                img.name = name
                img.pack()                        # kept inside the .blend only
                os.remove(jpg)

                verts, uvs, lls, faces = [], [], [], []
                for gy in range(gr + 1):
                    for gx in range(gr + 1):
                        lon, lat = lonlat_from_tile(cx + ncx * gx / gr, cy + ncy * gy / gr, z)
                        p = frame.to_scene(lon, lat)
                        p.z += ground.offset(p.x, p.y) + z_offset
                        verts.append(p[:])
                        uvs.append((gx / gr, 1 - gy / gr))
                        lls.append((lon, lat))
                for gy in range(gr):
                    for gx in range(gr):
                        a = gy * (gr + 1) + gx
                        quad = (a, a + gr + 1, a + gr + 2, a + 1)
                        if hole and all(hole[0] <= lls[q][0] <= hole[2] and hole[1] <= lls[q][1] <= hole[3]
                                        for q in quad):
                            continue          # a sharper layer covers this spot
                        faces.append(quad)
                if not faces:
                    bpy.data.images.remove(img)
                    continue
                me = bpy.data.meshes.new(name)
                me.from_pydata(verts, [], faces)
                uv = me.uv_layers.new(name="UVMap")
                for poly in me.polygons:
                    for li, vi in zip(poly.loop_indices, poly.vertices):
                        uv.data[li].uv = uvs[vi]
                me.validate()
                me.update()

                mat = bpy.data.materials.new(name)
                if bpy.app.version < (5, 0, 0):
                    mat.use_nodes = True
                nt = mat.node_tree
                bsdf = nt.nodes.get("Principled BSDF")
                tex = nt.nodes.new("ShaderNodeTexImage")
                tex.image = img
                tex.interpolation = "Linear"
                tex.extension = "EXTEND"
                tex.location = (-400, 300)
                nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
                bsdf.inputs["Roughness"].default_value = 1.0
                mat.diffuse_color = (0.5, 0.5, 0.5, 1)
                me.materials.append(mat)

                ob = bpy.data.objects.new(name, me)
                ob["nyc_basemap"] = layer
                ob["zoom"] = z
                coll.objects.link(ob)
                n_chunks += 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"  {n_chunks} pieces; {filled} tiles filled from a lower zoom, "
          f"{n_tiles - got_total} not needed or unavailable")
    if not got_total and not hole:
        raise RuntimeError("Google returned no satellite tiles for this area.")
    return google.copyright(z, box)


def fresh_collection(name):
    scn = bpy.context.scene
    coll = bpy.data.collections.get(name)
    if coll:
        for ob in list(coll.objects):
            me = ob.data
            bpy.data.objects.remove(ob, do_unlink=True)
            if me and me.users == 0:
                for mat in me.materials:
                    if mat and mat.users <= 1:
                        bpy.data.materials.remove(mat)
                bpy.data.meshes.remove(me)
    else:
        coll = bpy.data.collections.new(name)
    if coll.name not in scn.collection.children:
        scn.collection.children.link(coll)
    return coll


def set_collection_visible(name, visible):
    def walk(lc):
        if lc.collection.name == name:
            lc.hide_viewport = not visible
            return True
        return any(walk(c) for c in lc.children)
    walk(bpy.context.view_layer.layer_collection)
    coll = bpy.data.collections.get(name)
    if coll:
        coll.hide_render = not visible


def basemap_main():
    import numpy as np
    frame = detect_frame()
    clip = trees_area(frame)
    print(f"Trees area: lon {clip[0]:.5f}..{clip[2]:.5f}, lat {clip[1]:.5f}..{clip[3]:.5f}")
    google = GoogleSatellite(find_google_key())

    for old_name in OLD_BASEMAP_COLLECTIONS:          # basemaps from earlier versions of this script
        old = bpy.data.collections.get(old_name)
        if old:
            fresh_collection(old_name)
            bpy.data.collections.remove(old)
    coll = fresh_collection(BASEMAP_COLLECTION)
    for img in [i for i in bpy.data.images if i.name.startswith("NYC_Basemap_") and i.users == 0]:
        bpy.data.images.remove(img)

    ground = TreeGround(frame)
    city = (min(NYC_BBOX[0], clip[0]), min(NYC_BBOX[1], clip[1]), max(NYC_BBOX[2], clip[2]), max(NYC_BBOX[3], clip[3]))
    around = pad_box(clip, CONTEXT_PAD_M)
    credits = []
    # 1) sharp under the trees  2) 5 km around them  3) all of NYC to the horizon.
    #    Coarser layers sit a little lower and leave a hole where a sharper one is.
    credits.append(build_layer(np, coll, frame, ground, google, "trees", clip, ZOOM, MAX_TILES, BASEMAP_Z_OFFSET))
    try:
        credits.append(build_layer(np, coll, frame, ground, google, "surroundings", around, 16, 1400,
                                   BASEMAP_Z_OFFSET - 1.5, hole=clip, grid_res=64))
        credits.append(build_layer(np, coll, frame, ground, google, "city", city, 13, 400,
                                   BASEMAP_Z_OFFSET - 4.0, hole=around, grid_res=48))
    except Exception as e:
        print(f"Wider context skipped: {e}")
    set_collection_visible(BASEMAP_COLLECTION, True)

    if HIDE_OLD_BASEMAPS:
        for ob in bpy.context.scene.objects:
            if ob.name.startswith("EXPORT_"):
                ob.hide_set(True)
                ob.hide_render = True
    credit = "; ".join(sorted(set(c for c in credits if c))) or "Imagery (c) Google"
    coll["attribution"] = credit
    print("Google satellite basemap done. " + credit)
    return credit


def setup_look():
    """Light-grey sky and a soft sun so the genus colours read like the reference map."""
    scn = bpy.context.scene
    world = bpy.data.worlds.get("NYC Map Light") or bpy.data.worlds.new("NYC Map Light")
    if bpy.app.version < (5, 0, 0):
        world.use_nodes = True
    bg = world.node_tree.nodes.get("Background") if world.node_tree else None
    if bg:
        bg.inputs["Color"].default_value = (0.80, 0.82, 0.85, 1.0)
        bg.inputs["Strength"].default_value = 0.75
    world.color = (0.80, 0.82, 0.85)
    scn.world = world
    try:
        scn.view_settings.view_transform = "Standard"   # show legend colours as-is, like a web map
        scn.view_settings.look = "None"
    except Exception:
        pass
    sun = bpy.data.objects.get("NYC Sun")
    if sun is None:
        sun = bpy.data.objects.new("NYC Sun", bpy.data.lights.new("NYC Sun", "SUN"))
        scn.collection.objects.link(sun)
    sun.data.energy = 2.2
    sun.data.angle = math.radians(8)
    sun.rotation_euler = (math.radians(40), 0.0, math.radians(35))
    for name in ("Light", "Cube"):                       # default-scene clutter
        ob = scn.objects.get(name)
        if ob is not None and ob.type in ("LIGHT", "MESH") and ob.name == name and \
                (ob.type == "LIGHT" or len(ob.data.vertices) == 8):
            ob.hide_set(True)
            ob.hide_render = True


def setup_view():
    """Make the result visible: long view distance, Material Preview, zoom to the trees."""
    wm = bpy.context.window_manager
    targets = [o for n in ("NYC Trees",) if bpy.data.collections.get(n)
               for o in bpy.data.collections[n].objects]
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type != "VIEW_3D":
                continue
            space = area.spaces.active
            space.clip_start = 1.0
            space.clip_end = 100000.0
            space.shading.type = "MATERIAL"
            space.shading.use_scene_world = True      # bright, even light like the web map
            space.shading.use_scene_lights = True
            space.overlay.show_floor = False
            space.overlay.show_ortho_grid = False
            space.overlay.show_axis_x = False
            space.overlay.show_axis_y = False
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            if region is None or not targets:
                continue
            try:
                with bpy.context.temp_override(window=window, area=area, region=region):
                    bpy.ops.object.select_all(action="DESELECT")
                    for o in targets:
                        if o.visible_get():
                            o.select_set(True)
                    bpy.ops.view3d.view_selected()
                    bpy.ops.object.select_all(action="DESELECT")
            except Exception as e:
                print(f"(could not zoom the view automatically: {e})")


def report(msg):
    print(msg)
    if bpy.app.background or not bpy.context.window_manager.windows:
        return
    try:
        bpy.context.window_manager.popup_menu(lambda self, ctx: [self.layout.label(text=l) for l in msg.split("\n")],
                                              title="NYC trees", icon="INFO")
    except Exception:
        pass


# ================= run =================
trees_main()
basemap_msg = "Google satellite basemap added."
try:
    basemap_msg = "Google satellite basemap added.\n" + basemap_main()
except Exception as e:      # trees are already in; don't lose them over a download problem
    import traceback
    traceback.print_exc()
    basemap_msg = f"Basemap skipped: {e}"
    print(basemap_msg)
try:
    setup_look()
except Exception as e:
    print(f"(lighting setup skipped: {e})")
try:
    setup_view()
except Exception as e:
    print(f"(view setup skipped: {e})")
n_trees = sum(len(o.data.polygons) > 0 for o in bpy.data.collections["NYC Trees"].objects) \
    if bpy.data.collections.get("NYC Trees") else 0
report(f"Finished: trees imported ({n_trees} genus objects).\n{basemap_msg}")
