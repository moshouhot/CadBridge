# Source register - human summary

Audit id: 20260917T083402Z  |  raw JSON: `source-register.json`  |  all data fetched over the network at audit time; nothing installed, no CAD process touched.

## Verification counts

- GitHub sources audited: 7 of 7 resolved and fetched; **7 VERIFIED, 0 UNVERIFIED** (licenses: 4x MIT, 2x Apache-2.0, 1x NOASSERTION-by-construction).
- NuGet packages audited: **13 of 13 VERIFIED, 0 UNVERIFIED** (nuspec + real .nupkg contents read for every one).
- Local ObjectARX SDK trees scanned: 6 of 6.
- Open failures recorded in `failures[]`: see JSON; currently empty.

## GitHub sources

| ID | Repository | HEAD commit | LICENSE | status | latest release | NOTICE |
|---|---|---|---|---|---|---|
| S03_S04_AutoLispExt | Autodesk-AutoCAD/AutoLispExt | `74f59ee220a3` | LICENSE.md (blob `11069edd7901`) | VERIFIED-Apache-2.0 | v1.6.2-3f1f12f50397a4d30acf6a9df2bc26da332d62bf @ 2023-04-13 | NOTICE.md |
| S07_csharp-sdk | modelcontextprotocol/csharp-sdk | `324ccd83c357` | LICENSE (blob `185054bef9df`) | VERIFIED-NOASSERTION | v2.2.0 @ 2026-08-13 | THIRD-PARTY-NOTICES.txt |
| S09_batchPrintZWCAD | moshouhot/batchPrintZWCAD | `6ded809582f9` | LICENSE (blob `21f0e5bde617`) | VERIFIED-MIT | NONE (404 from /releases/latest) | none |
| S10_Autocad-MCP | U-C4N/Autocad-MCP | `abc2a82e7128` | LICENSE (blob `745aae3580c1`) | VERIFIED-MIT | v1.5.1 @ 2026-08-07 | none |
| S11_mcp-scout | mcp-scout/mcp-scout | `76b28888dc85` | LICENSE (blob `699fcda9d7d9`) | VERIFIED-MIT | v0.1.3 @ 2026-07-21 | none |
| S13_beiming | beiming183-cloud/AutoCAD-MCP | `11f7c47e5038` | LICENSE (blob `527d3a3bdde9`) | VERIFIED-MIT | v3.10.1 @ 2026-07-17 | none |
| S14_dwg-mcp | bimwright/dwg-mcp | `c04af1ff5ae3` | LICENSE (blob `267d520a2c99`) | VERIFIED-Apache-2.0 | v1.0.0 @ 2026-08-28 | THIRD_PARTY_NOTICES.md |

Notes: `mcp-scout/mcp-scout` **does exist** (the brief allowed for it being absent); all 7 repositories are non-archived.
`moshouhot/batchPrintZWCAD` has **no release at all** (`/releases/latest` -> 404) although it does carry a LICENSE (MIT).
`Autodesk-AutoCAD/AutoLispExt` latest release is from 2023 while its default branch was pushed 2026-05, i.e. the release tag is stale.

All eight blob SHAs recorded in DESIGN.md D17 still reproduce exactly at the audited default-branch HEADs - no source drift since D17.

## NuGet packages

| Package | version audited | license (from nuspec) | TFMs shipped | deprecated |
|---|---|---|---|---|
| ModelContextProtocol | 2.2.0 | expression: Apache-2.0 | net10.0,net8.0,net9.0,netstandard2.0 | no |
| ModelContextProtocol.AspNetCore | 2.2.0 | expression: Apache-2.0 | net10.0,net8.0,net9.0 | no |
| Microsoft.Extensions.Logging | 10.0.12 | expression: MIT | net10.0,net462,net8.0,net9.0,netstandard2.0,netstandard2.1 | no |
| Microsoft.Extensions.Logging.Console | 10.0.12 | expression: MIT | net10.0,net462,net8.0,net9.0,netstandard2.0 | no |
| Serilog | 4.4.0 | expression: Apache-2.0 | net10.0,net462,net471,net6.0,net8.0,net9.0,netstandard2.0 | no |
| Serilog.Extensions.Logging | 10.0.0 | expression: Apache-2.0 | net10.0,net462,net8.0,net9.0,netstandard2.0,netstandard2.1 | no |
| Serilog.Sinks.File | 7.0.0 | expression: Apache-2.0 | net462,net471,net6.0,net8.0,net9.0,netstandard2.0 | no |
| System.Text.Json | 10.0.12 | expression: MIT | net10.0,net462,net8.0,net9.0,netstandard2.0 | no |
| Newtonsoft.Json | 13.0.4 | expression: MIT | net20,net35,net40,net45,net6.0,netstandard1.0,netstandard1.3,netstandard2.0 | no |
| Lucene.Net | 4.8.0-beta00018 | file: LICENSE.txt | net462,net6.0,net8.0,netstandard2.0,netstandard2.1 | no |
| Lucene.Net.Analysis.Common | 4.8.0-beta00018 | file: LICENSE.txt | net462,net6.0,net8.0,netstandard2.0,netstandard2.1 | no (audited 4.8.0-beta00018); YES for 4.9.0 |
| Lucene.Net.QueryParser | 4.8.0-beta00018 | file: LICENSE.txt | net462,net6.0,net8.0,netstandard2.0,netstandard2.1 | no |
| AutoCAD.NET | 26.0.0 | file: LICENSE.txt | net10.0 | no |

## S03/S04 - AutoLISP debug adapter (verified from source at the pinned commit)

Both files requested by the brief exist and their blob SHAs still equal the values recorded in DESIGN.md D17:
`extension/src/debug.ts` = `33f37992fe82aa4f07ae61f107b232d6f535a7de`, `extension/src/platform.ts` =
`3be5ead26ecb55f028548507ec102fe71a3e13bd`. The adapter executable is resolved by `calculateABSPathForDAP(productPath)` in
`platform.ts` (`path.dirname` of the AutoCAD executable the user picked in launch.json, then `Help/` for the macOS variant):

```ts
// extension/src/platform.ts, lines 14-17
if(platform === 'Windows_NT'){
    return folder + "\AutoLispDebugAdapter.exe";
}else if(platform === 'Darwin'){
	return folder + "/../Helpers/AutoLispDebugAdapter.app/Contents/MacOS/AutoLispDebugAdapter";
```

`debug.ts` only consumes that path (`let lispadapterpath = calculateABSPathForDAP(productPath);` at line 176 and
`... = calculateABSPathForDAP(ProcessPathCache.globalProductPath);` at line 224, cached in `ProcessPathCache.globalLispAdapterPath`
and handed to `new vscode.DebugAdapterExecutable(lispadapterpath, args)` at lines 44 (launch, args `["--", product, params]`)
and 56 (attach, no args)). The adapter binary is **not** in the repository - it ships with AutoCAD 2021+.

## AutoCAD.NET version history on the feed (Why 26.0.0 is not the one to bind)

`AutoCAD.NET` on nuget.org carries 20.0.0, 20.0.1, 20.1.0, 21.0.0, 21.0.1, 21.0.2 ... 26.0.0 (merged count 19).
Each major maps to an AutoCAD release: 20.x=2015/2016, 21.x=2017, 22.x=2018, 23.x=2019, 24.x=2020/2021, 25.x=2022-2026,
26.0.0=AutoCAD 2027 (.NET 10). Versions 20.0.x are the ones the local ObjectARX-2014/2017 SDK trees correspond to; **the local
SDK trees and the NuGet feed are two separate distribution channels for the same Autodesk EULA.**

## Autodesk redistribution findings

**AutoCAD.NET (NuGet)** - CORRECTION to the audit brief: this is *not* a community repackaging. The nuspec says
`<owners>Autodesk, Inc.</owners>`, and the 6.4 kB `LICENSE.txt` inside the .nupkg *is* the Autodesk ObjectARX EULA
("For ObjectARX(R) for AutoCAD(R) 2024, 2023, 2022, and 2021"). That EULA allows unlimited copies *only* "to develop
applications for Autodesk products based on the AutoCAD(R) platform", requires every copy to carry the agreement,
and forbids use for AutoCAD LT / DWG TrueConvert / DWG TrueView. The package itself sets `CopyLocal=false` for all ten
managed assemblies via `tools/install.ps1`, i.e. Autodesk does not intend these DLLs to be shipped with your product.
**Your ObjectARX SDK install has NO license/redistributable text file at all** - no LICENSE*, NOTICE*, EULA*, or
Redistrib*.txt anywhere in any of the six SDK versions; only `Redistrib-win32/` + `Redistrib-x64/` directories whose
names resemble a redistribution grant, and a copyright header inside the 65 sample readme.txt files.

## FILLET / round-corner discovery dictionary (U-C4N/Autocad-MCP)

No English+Chinese dictionary exists. `discovery/aliases.py` (MIT, blob `f1f0020bd0a8c2335c441b223b73023647ac640b`,
42 217 bytes) is a pure **English** AutoCAD-command / natural-language alias corpus. A full-tree scan for CJK
characters (U+4E00-U+9FFF) over all 199 tracked files returned **0 matches** - the repository has no Chinese content.
The FILLET entry is `TOOL_ALIASES["entity_fillet"] = ToolAliases(acad=("FILLET",), synonyms=("fillet", "round the corner",
"rounded corner", "radius the corner", "tangent arc corner"))` - that is the only "round corner" vocabulary available.

## Single most important risk flag

**Autodesk assembly redistribution / runtime binding is the release blocker.** Every managed AutoCAD assembly the
product will bind against (the six local ObjectARX SDK trees and the AutoCAD.NET NuGet feed) is governed by the Autodesk
ObjectARX EULA, which grants development use only, requires the agreement to travel with every copy, and (in the NuGet
package) explicitly disables CopyLocal. None of the Apache-2.0 / MIT upstreams audited here lift that constraint, so the
release package must not carry Autodesk DLLs and must resolve them from the installed AutoCAD; a written Autodesk
redistribution grant is still UNVERIFIED and is the gate for G06.

Secondary flag (supply-chain / build): the Lucene.Net family has **no stable release at all** (`Lucene.Net 4.8.0-beta00018`,
`Lucene.Net.QueryParser 4.8.0-beta00018`), and the newest `Lucene.Net.Analysis.Common` (4.9.0) is **deprecated, unlisted,
net451-only and a third-party recompile** (reasons `Legacy`, `CriticalBugs`). Pin all three to `4.8.0-beta00018`.

Third flag (TFM): **no audited package ships a `net48` asset.** `Microsoft.Extensions.*`, `System.Text.Json`, `Serilog.*`,
`Newtonsoft.Json`, the MCP SDK and Lucene.Net all resolve down to `netstandard2.0` / `net462`; `ModelContextProtocol` and
`ModelContextProtocol.AspNetCore` have **no .NET Framework asset at all** (netstandard2.0+ only), and `AutoCAD.NET 26.0.0`
ships **only `lib/net10.0`**. A net48 plugin must therefore use AutoCAD.NET 24.x/25.x, and any net48 consumer of the MCP SDK
(if Microsoft.Extensions >= 10 is involved) is not supported by these packages.
