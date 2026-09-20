using System;

namespace Autodesk.AutoCAD.DatabaseServices
{
    internal sealed class NamespaceMarker { }
}

namespace Autodesk.AutoCAD.Runtime
{
    [AttributeUsage(AttributeTargets.Method)]
    internal sealed class CommandMethodAttribute : Attribute
    {
        public CommandMethodAttribute(string name) { }
    }
}

namespace Autodesk.AutoCAD.ApplicationServices.Core
{
    internal static class Application
    {
        internal static StubDocumentManager DocumentManager { get; } = new StubDocumentManager();
    }

    internal sealed class StubDocumentManager
    {
        internal bool IsApplicationContext { get; set; }
        internal object MdiActiveDocument { get; set; }
    }
}
