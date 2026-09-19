// CadBridge shared plugin source. Compiled into BOTH the Legacy (net48) and
// Modern (net8.0-windows) shells. This file must only use APIs present in the
// LOWEST supported target (AutoCAD 2015 / .NET Framework 4.8).
//
// P1/T01-1 scope: report the ACTUAL runtime so evidence can never be satisfied by a
// build-time constant.
using System;
using System.Reflection;
using System.Runtime.InteropServices;

namespace CadBridge.Plugin.Shared
{
    /// <summary>
    /// Runtime reporting for evidence capture.
    ///
    /// DESIGN NOTE (corrected after review): an earlier version returned a literal string
    /// for the .NET Framework case. That made a *compiled-in constant* look like a measured
    /// fact, and it invited the false conclusion that the two shells ran on different
    /// runtimes when both were actually on .NET 8. This type now reports three separate,
    /// non-interchangeable things:
    ///
    ///   CompiledTarget       - what the assembly was built for. A constant, but clearly
    ///                          labelled as such.
    ///   RuntimeFramework     - what the CLR actually reports at run time.
    ///   ClrVersion           - Environment.Version, i.e. the executing CLR.
    ///
    /// Never derive a runtime claim from CompiledTarget.
    /// </summary>
    public static class RuntimeInfo
    {
        /// <summary>Executing CLR version, reported by the runtime itself.</summary>
        public static string ClrVersion => Environment.Version.ToString();

        /// <summary>
        /// What the CLR reports about its own framework. Available on .NET Framework 4.7.1+
        /// and on .NET Core / .NET 5+, so it works for both shells and is genuinely measured.
        /// </summary>
        public static string RuntimeFramework
        {
            get
            {
                try
                {
                    return RuntimeInformation.FrameworkDescription;
                }
                catch (Exception ex)
                {
                    return "(error: " + ex.GetType().Name + ")";
                }
            }
        }

        /// <summary>
        /// What this assembly was compiled against. This is a build-time constant and is
        /// labelled as such wherever it is reported.
        /// </summary>
        public static string CompiledTarget =>
#if NETFRAMEWORK
            "net48";
#else
            "net8.0-windows";
#endif

        /// <summary>True when the executing CLR is .NET Framework rather than .NET (Core).</summary>
        public static bool IsFrameworkClr
        {
            get
            {
                try
                {
                    // On .NET 5+ this type lives in System.Runtime; on .NET Framework it is
                    // absent from the runtime assembly set.
                    return Type.GetType("System.Runtime.InteropServices.RuntimeInformation")
                           != null
                           && RuntimeInformation.FrameworkDescription.StartsWith(
                                  ".NET Framework", StringComparison.OrdinalIgnoreCase);
                }
                catch
                {
                    return false;
                }
            }
        }

        public static string ProcessArchitecture =>
            Environment.Is64BitProcess ? "x64" : "x86";

        public static string AssemblyPath
        {
            get
            {
                try
                {
                    var asm = Assembly.GetExecutingAssembly();
                    return string.IsNullOrEmpty(asm.Location) ? "(no location)" : asm.Location;
                }
                catch (Exception ex)
                {
                    return "(error: " + ex.GetType().Name + ")";
                }
            }
        }

        public static string AssemblyFileVersion
        {
            get
            {
                try
                {
                    var asm = Assembly.GetExecutingAssembly();
                    var fv = asm.GetCustomAttribute<AssemblyFileVersionAttribute>();
                    return fv != null ? fv.Version : asm.GetName().Version.ToString();
                }
                catch (Exception ex)
                {
                    return "(error: " + ex.GetType().Name + ")";
                }
            }
        }

        /// <summary>One-line, machine-greppable summary for evidence capture.</summary>
        public static string Summary()
        {
            return string.Join("; ",
                "compiled_target=" + CompiledTarget,
                "clr=" + ClrVersion,
                "runtime_framework=" + RuntimeFramework,
                "framework_clr=" + IsFrameworkClr,
                "arch=" + ProcessArchitecture,
                "asm=" + AssemblyPath,
                "asmver=" + AssemblyFileVersion);
        }
    }
}
