using System;
using System.Collections.Generic;
using Autodesk.Revit.DB;
using Autodesk.Revit.DB.DirectContext3D;
using Autodesk.Revit.DB.ExternalService;
using Autodesk.Revit.UI;

namespace ClashViewSync.DirectContext
{
    public class ClashMesh
    {
        public List<XYZ> Vertices { get; set; }
        public List<int> Triangles { get; set; } // groups of 3 indices
        public byte Red { get; set; }
        public byte Green { get; set; }
        public byte Blue { get; set; }
        public byte Transparency { get; set; }

        public ClashMesh()
        {
            Vertices = new List<XYZ>();
            Triangles = new List<int>();
            Transparency = 40;
        }
    }

    internal class BufferPair : IDisposable
    {
        public VertexBuffer Vb;
        public IndexBuffer Ib;
        public int VertexCount;
        public int TriangleCount;
        public VertexFormat Format;
        public EffectInstance Effect;

        public void Dispose()
        {
            if (Vb != null && Vb.IsValidObject) { Vb.Dispose(); Vb = null; }
            if (Ib != null && Ib.IsValidObject) { Ib.Dispose(); Ib = null; }
            if (Format != null && Format.IsValidObject) { Format.Dispose(); Format = null; }
            if (Effect != null && Effect.IsValidObject) { Effect.Dispose(); Effect = null; }
        }
    }

    public class ClashDirectContextServer : IDirectContext3DServer
    {
        private static readonly Guid ServerId = new Guid("8E4B7C92-3F1D-4A65-9B2E-7C5D1A8F0E55");

        public ElementId TargetViewId { get; set; }
        public Outline BoundingBox { get; set; }
        public List<ClashMesh> Meshes { get; set; }

        private readonly List<BufferPair> _buffers = new List<BufferPair>();
        private bool _dirty = true;
        private readonly object _lock = new object();

        public ClashDirectContextServer()
        {
            Meshes = new List<ClashMesh>();
        }

        public Guid GetServerId()
        {
            return ServerId;
        }

        public ExternalServiceId GetServiceId()
        {
            return ExternalServices.BuiltInExternalServices.DirectContext3DService;
        }

        public string GetName()
        {
            return "ClashView Transient Highlighter";
        }

        public string GetVendorId()
        {
            return "SAKR";
        }

        public string GetDescription()
        {
            return "Transient GPU clash participant highlighting (zero DB modification)";
        }

        public string GetApplicationId()
        {
            return "ClashViewSync";
        }

        public string GetSourceId()
        {
            return "";
        }

        public bool UsesHandles()
        {
            return false;
        }

        public bool UseInTransparentPass(View v)
        {
            return true;
        }

        public bool CanExecute(View v)
        {
            return TargetViewId != null && v != null && v.Id == TargetViewId && Meshes != null && Meshes.Count > 0;
        }

        public Outline GetBoundingBox(View v)
        {
            if (BoundingBox != null)
            {
                return BoundingBox;
            }
            return new Outline(new XYZ(-10000, -10000, -10000), new XYZ(10000, 10000, 10000));
        }

        public void MarkDirty()
        {
            lock (_lock)
            {
                _dirty = true;
            }
        }

        public void ClearBuffers()
        {
            lock (_lock)
            {
                foreach (BufferPair b in _buffers)
                {
                    b.Dispose();
                }
                _buffers.Clear();
                _dirty = true;
            }
        }

        public void RenderScene(View v, DisplayStyle ds)
        {
            try
            {
                lock (_lock)
                {
                    if (_dirty)
                    {
                        BuildBuffers();
                        _dirty = false;
                    }

                    if (_buffers.Count == 0)
                    {
                        return;
                    }

                    foreach (BufferPair bp in _buffers)
                    {
                        if (bp.Vb != null && bp.Ib != null && bp.TriangleCount > 0)
                        {
                            DrawContext.FlushBuffer(
                                bp.Vb,
                                bp.VertexCount,
                                bp.Ib,
                                bp.TriangleCount * 3,
                                bp.Format,
                                bp.Effect,
                                PrimitiveType.TriangleList,
                                0,
                                bp.TriangleCount
                            );
                        }
                    }
                }
            }
            catch
            {
                // Never throw from inside RenderScene
            }
        }

        private void BuildBuffers()
        {
            foreach (BufferPair b in _buffers)
            {
                b.Dispose();
            }
            _buffers.Clear();

            if (Meshes == null || Meshes.Count == 0)
            {
                return;
            }

            foreach (ClashMesh mesh in Meshes)
            {
                if (mesh.Vertices == null || mesh.Vertices.Count == 0 ||
                    mesh.Triangles == null || mesh.Triangles.Count == 0)
                {
                    continue;
                }

                int vCount = mesh.Vertices.Count;
                int triCount = mesh.Triangles.Count / 3;
                if (triCount == 0)
                {
                    continue;
                }

                BufferPair bp = new BufferPair();
                bp.VertexCount = vCount;
                bp.TriangleCount = triCount;
                bp.Format = new VertexFormat(VertexFormatBits.PositionColored);
                bp.Effect = new EffectInstance(VertexFormatBits.PositionColored);

                int vertexSizeFloats = VertexPositionColored.GetSizeInFloats();
                int totalFloats = vCount * vertexSizeFloats;
                bp.Vb = new VertexBuffer(totalFloats);
                bp.Vb.Map(totalFloats);

                ColorWithTransparency color = new ColorWithTransparency((uint)mesh.Red, (uint)mesh.Green, (uint)mesh.Blue, (uint)mesh.Transparency);
                using (VertexStreamPositionColored vStream = bp.Vb.GetVertexStreamPositionColored())
                {
                    for (int i = 0; i < vCount; i++)
                    {
                        vStream.AddVertex(new VertexPositionColored(mesh.Vertices[i], color));
                    }
                }
                bp.Vb.Unmap();

                int indexSizeShorts = IndexTriangle.GetSizeInShortInts();
                int totalShorts = triCount * indexSizeShorts;
                bp.Ib = new IndexBuffer(totalShorts);
                bp.Ib.Map(totalShorts);

                using (IndexStreamTriangle iStream = bp.Ib.GetIndexStreamTriangle())
                {
                    for (int t = 0; t < triCount; t++)
                    {
                        int i0 = mesh.Triangles[t * 3];
                        int i1 = mesh.Triangles[t * 3 + 1];
                        int i2 = mesh.Triangles[t * 3 + 2];
                        iStream.AddTriangle(new IndexTriangle(i0, i1, i2));
                    }
                }
                bp.Ib.Unmap();

                _buffers.Add(bp);
            }
        }
    }

    public static class ClashHighlighter
    {
        private static ClashDirectContextServer _server;
        private static bool _isRegistered = false;

        public static ClashDirectContextServer GetServer()
        {
            if (_server == null)
            {
                _server = new ClashDirectContextServer();
            }
            return _server;
        }

        public static void EnsureRegistered()
        {
            ClashDirectContextServer server = GetServer();
            MultiServerService service = (MultiServerService)ExternalServiceRegistry.GetService(server.GetServiceId());
            if (service == null)
            {
                return;
            }

            if (!service.IsRegisteredServerId(server.GetServerId()))
            {
                service.AddServer(server);
            }

            IList<Guid> activeServers = service.GetActiveServerIds();
            if (!activeServers.Contains(server.GetServerId()))
            {
                activeServers.Add(server.GetServerId());
                service.SetActiveServers(activeServers);
            }
            _isRegistered = true;
        }

        public static void Unregister()
        {
            if (_server == null)
            {
                return;
            }

            try
            {
                MultiServerService service = (MultiServerService)ExternalServiceRegistry.GetService(_server.GetServiceId());
                if (service != null && service.IsRegisteredServerId(_server.GetServerId()))
                {
                    IList<Guid> activeServers = service.GetActiveServerIds();
                    if (activeServers.Contains(_server.GetServerId()))
                    {
                        activeServers.Remove(_server.GetServerId());
                        service.SetActiveServers(activeServers);
                    }
                    service.RemoveServer(_server.GetServerId());
                }
            }
            catch { }

            _server.ClearBuffers();
            _isRegistered = false;
        }

        public static void Clear(UIDocument uidoc)
        {
            if (_server != null)
            {
                _server.ClearBuffers();
                _server.TargetViewId = null;
                _server.Meshes.Clear();
            }
            if (uidoc != null)
            {
                try
                {
                    uidoc.RefreshActiveView();
                }
                catch { }
            }
        }

        public static void ShowMeshes(UIDocument uidoc, View view, IList<ClashMesh> meshes, Outline bbox)
        {
            if (view == null)
            {
                return;
            }

            EnsureRegistered();
            ClashDirectContextServer server = GetServer();
            server.TargetViewId = view.Id;
            server.BoundingBox = bbox;
            server.Meshes = new List<ClashMesh>(meshes);
            server.MarkDirty();

            if (uidoc != null)
            {
                try
                {
                    uidoc.RefreshActiveView();
                }
                catch { }
            }
        }

        public static ClashMesh ExtractElementMesh(Element elem, Transform transform, byte r, byte g, byte b, byte transparency, double scaleOffset)
        {
            ClashMesh result = new ClashMesh();
            result.Red = r;
            result.Green = g;
            result.Blue = b;
            result.Transparency = transparency;

            if (elem == null)
            {
                return result;
            }

            Options opts = new Options();
            opts.DetailLevel = ViewDetailLevel.Fine;
            opts.ComputeReferences = false;
            opts.IncludeNonVisibleObjects = false;

            GeometryElement geo = elem.get_Geometry(opts);
            if (geo == null)
            {
                return result;
            }

            List<Solid> solids = new List<Solid>();
            CollectSolids(geo, solids);

            Transform xf = transform != null ? transform : Transform.Identity;

            int vertOffset = 0;
            foreach (Solid solid in solids)
            {
                if (solid.Faces.Size == 0 || solid.Volume <= 1e-9)
                {
                    continue;
                }

                foreach (Face face in solid.Faces)
                {
                    Mesh mesh = face.Triangulate();
                    if (mesh == null)
                    {
                        continue;
                    }

                    XYZ normal = XYZ.BasisZ;
                    try
                    {
                        BoundingBoxUV uvBox = face.GetBoundingBox();
                        double midU = (uvBox.Min.U + uvBox.Max.U) * 0.5;
                        double midV = (uvBox.Min.V + uvBox.Max.V) * 0.5;
                        normal = face.ComputeNormal(new UV(midU, midV));
                    }
                    catch
                    {
                        normal = XYZ.BasisZ;
                    }

                    XYZ worldNormal = xf.OfVector(normal).Normalize();
                    double delta = scaleOffset > 0 ? scaleOffset : 0.003;
                    XYZ offsetVec = worldNormal.Multiply(delta);

                    int numVerts = mesh.Vertices.Count;
                    for (int i = 0; i < numVerts; i++)
                    {
                        XYZ pt = mesh.Vertices[i];
                        result.Vertices.Add(xf.OfPoint(pt).Add(offsetVec));
                    }

                    int numTris = mesh.NumTriangles;
                    for (int t = 0; t < numTris; t++)
                    {
                        MeshTriangle tri = mesh.get_Triangle(t);
                        result.Triangles.Add(vertOffset + (int)tri.get_Index(0));
                        result.Triangles.Add(vertOffset + (int)tri.get_Index(1));
                        result.Triangles.Add(vertOffset + (int)tri.get_Index(2));
                    }

                    vertOffset += numVerts;
                }
            }

            return result;
        }

        private static void CollectSolids(GeometryElement geo, List<Solid> solids)
        {
            foreach (GeometryObject obj in geo)
            {
                Solid s = obj as Solid;
                if (s != null && s.Volume > 1e-9 && s.Faces.Size > 0)
                {
                    solids.Add(s);
                }
                else
                {
                    GeometryInstance gi = obj as GeometryInstance;
                    if (gi != null)
                    {
                        CollectSolids(gi.GetInstanceGeometry(), solids);
                    }
                }
            }
        }
    }
}
