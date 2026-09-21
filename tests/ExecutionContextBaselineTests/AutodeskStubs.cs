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
        private object _mdiActiveDocument;

        internal bool IsApplicationContext { get; set; }
        internal bool ThrowOnMdiActiveDocumentGet { get; set; }

        internal object MdiActiveDocument
        {
            get
            {
                if (ThrowOnMdiActiveDocumentGet)
                {
                    throw new InvalidOperationException("injected MdiActiveDocument getter failure");
                }
                return _mdiActiveDocument;
            }
            set { _mdiActiveDocument = value; }
        }
    }
}
