"""Actual pxr import, scene round trips and native plugin/resource loading."""
import json
from pathlib import Path
import sys

prefix, output, materialx, alembic, openusd_source = map(Path, sys.argv[1:])
sys.path.insert(0, str(prefix / "lib/python"))
from pxr import Usd, UsdGeom, Gf, Vt, Plug, Sdf, Tf, UsdShade, UsdMtlx

assert Usd.GetVersion() == (0, 24, 5), Usd.GetVersion()
# Exercise C++ stream/locale state after the larger Usd extension loads first.
assert str(Gf.Vec3f(1, 2, 3)) == "(1, 2, 3)"
stage = Usd.Stage.CreateInMemory()
UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
UsdGeom.SetStageMetersPerUnit(stage, 0.01)
root = UsdGeom.Xform.Define(stage, "/World")
stage.SetDefaultPrim(root.GetPrim())
mesh = UsdGeom.Mesh.Define(stage, "/World/Mesh")
points = [Gf.Vec3f(0, 0, 0), Gf.Vec3f(100, 0, 0), Gf.Vec3f(0, 100, 0)]
mesh.CreatePointsAttr(Vt.Vec3fArray(points))
assert mesh.GetPointsAttr().Set(points), "Python list to Vt array conversion failed"
mesh.CreateFaceVertexCountsAttr(Vt.IntArray([3]))
mesh.CreateFaceVertexIndicesAttr(Vt.IntArray([0, 1, 2]))
mesh.AddTranslateOp().Set(Gf.Vec3d(7, 11, 13))
material = UsdShade.Material.Define(stage, "/World/Material")
shader = UsdShade.Shader.Define(stage, "/World/Material/Preview")
shader.CreateIdAttr("UsdPreviewSurface")
shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.2, 0.5, 0.7))
material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(material)
output.mkdir(parents=True, exist_ok=True)
for extension in ("usda", "usdc"):
    filename = output / ("scene." + extension)
    assert stage.GetRootLayer().Export(str(filename))
    reopened = Usd.Stage.Open(str(filename))
    loaded = UsdGeom.Mesh(reopened.GetPrimAtPath("/World/Mesh"))
    assert list(loaded.GetPointsAttr().Get()) == points
    assert list(loaded.GetFaceVertexCountsAttr().Get()) == [3]
    assert list(loaded.GetFaceVertexIndicesAttr().Get()) == [0, 1, 2]
    assert loaded.ComputeLocalToWorldTransform(Usd.TimeCode.Default()).ExtractTranslation() == Gf.Vec3d(7, 11, 13)
    bound, _ = UsdShade.MaterialBindingAPI(loaded).ComputeBoundMaterial()
    assert str(bound.GetPath()) == "/World/Material"
registry = Plug.Registry()
plugin = registry.GetPluginWithName("usdAbc")
assert plugin and plugin.Load(), "Alembic plugin did not load"
abc = Usd.Stage.Open(str(alembic / "outputs/mesh.abc"))
assert abc and len(list(abc.Traverse())) > 0, "native Ogawa archive did not open through USD"
mtlx_format = Sdf.FileFormat.FindByExtension("mtlx")
assert mtlx_format, "MaterialX file format was not registered"
standard_mtlx = openusd_source / "pxr/usd/usdMtlx/testenv/testUsdMtlxFileFormat.testenv/GraphlessNodes.mtlx"
mtlx = Sdf.Layer.FindOrOpen(str(standard_mtlx))
assert mtlx and mtlx.rootPrims, "native MaterialX XML did not open through USD"
bad = output / "invalid.usda"
bad.write_text("not a USD layer\n")
rejected = False
try:
    rejected = Usd.Stage.Open(str(bad)) is None
except Tf.ErrorException:
    rejected = True
assert rejected, "malformed scene was accepted"
native = []
for module in tuple(sys.modules.values()):
    filename = getattr(module, "__file__", None)
    if filename and filename.endswith(".so") and str(prefix) in filename:
        native.append(filename)
assert native, "no native pxr extensions loaded"
result = {"version": list(Usd.GetVersion()), "formats": ["usda", "usdc", "abc", "mtlx"],
          "mesh_points": 3, "material_binding": True, "transformed_mesh": True,
          "malformed_rejected": rejected, "pxr_extensions": sorted(set(native)),
          "alembic_plugin_loaded": plugin.isLoaded}
(output / "smoke.json").write_text(json.dumps(result, indent=2) + "\n")
print("OpenUSD 24.05 native smoke PASS USDA/USDC/Alembic/MaterialX/Python")
