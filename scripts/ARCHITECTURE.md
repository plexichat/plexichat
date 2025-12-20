# Type Checking System Architecture

## System Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                     PlexiChat Type Check System                     │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                          INPUT: Repositories                        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐            │
│  │  plexichat   │   │ common-utils │   │  encryption  │            │
│  │   (main)     │   │  (submodule) │   │   (module)   │            │
│  └──────────────┘   └──────────────┘   └──────────────┘            │
│         │                   │                   │                    │
│         └───────────────────┴───────────────────┘                    │
│                             │                                        │
└─────────────────────────────┼────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    CONFIGURATION & EXECUTION                        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│               ┌──────────────────────────┐                           │
│               │  pyrightconfig.json      │                           │
│               │  - Type checking rules   │                           │
│               │  - Python version        │                           │
│               │  - Include/exclude paths │                           │
│               └────────────┬─────────────┘                           │
│                            │                                          │
│                            ▼                                          │
│         ┌──────────────────────────────────────┐                     │
│         │    pyright (via subprocess)          │                     │
│         │    - Runs on each repository         │                     │
│         │    - Outputs JSON diagnostics        │                     │
│         └────────────┬─────────────────────────┘                     │
│                      │                                                │
└──────────────────────┼────────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────────┐
│                          ORCHESTRATOR                               │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  ┌────────────────────────────────────────────────────────┐         │
│  │  TypeCheckOrchestrator                                 │         │
│  │  - Runs pyright on each repo                           │         │
│  │  - Parses JSON output                                  │         │
│  │  - Categorizes issues (Optional, Import, Return, etc.) │         │
│  │  - Aggregates statistics                               │         │
│  └────────────┬───────────────────────────────────────────┘         │
│               │                                                       │
└───────────────┼───────────────────────────────────────────────────────┘
                │
                ├──────────────────┬────────────────────┐
                ▼                  ▼                    ▼
┌─────────────────────┐  ┌─────────────────┐  ┌─────────────────────┐
│  MARKDOWN REPORTS   │  │  JSON OUTPUT    │  │  CONSOLE OUTPUT     │
├─────────────────────┤  ├─────────────────┤  ├─────────────────────┤
│                     │  │                 │  │                     │
│ Consolidated        │  │ Structured JSON │  │ Quick Summary       │
│ Summary             │  │ - Metadata      │  │ - Repo status       │
│ - Overall stats     │  │ - Per-repo data │  │ - Error counts      │
│ - By category       │  │ - Categories    │  │ - Warnings          │
│ - Priorities        │  │ - Aggregations  │  │                     │
│ - Hotspots          │  │ - Hotspots      │  │                     │
│ - Recommendations   │  │                 │  │                     │
│                     │  │                 │  │                     │
│ Individual Reports  │  │ Schema:         │  │                     │
│ - plexichat.md      │  │ report_schema   │  │                     │
│ - common-utils.md   │  │ .json           │  │                     │
│ - encryption.md     │  │                 │  │                     │
│                     │  │                 │  │                     │
│ Location:           │  │ Usage:          │  │ Usage:              │
│ type_check_reports/ │  │ CI/CD pipelines │  │ Developer feedback  │
│                     │  │ Dashboards      │  │                     │
│                     │  │ Metrics         │  │                     │
└─────────────────────┘  └─────────────────┘  └─────────────────────┘
```

## Component Details

### 1. Input Repositories

```
plexichat/
├── src/
│   ├── api/           ← Type checked
│   ├── core/          ← Type checked
│   ├── utils/
│   │   ├── common-utils/  ← Submodule (type checked separately)
│   │   └── encryption/    ← Module (type checked separately)
│   └── tests/         ← Excluded from type checking
└── pyrightconfig.json ← Shared config for all repos
```

### 2. Type Checking Pipeline

```
┌──────────────┐
│ Repository 1 │
│ (plexichat)  │
└──────┬───────┘
       │
       ├─► Run pyright ──► Parse JSON ──► Categorize Issues
       │
       ▼
┌──────────────┐
│ Repository 2 │
│(common-utils)│
└──────┬───────┘
       │
       ├─► Run pyright ──► Parse JSON ──► Categorize Issues
       │
       ▼
┌──────────────┐
│ Repository 3 │
│ (encryption) │
└──────┬───────┘
       │
       ├─► Run pyright ──► Parse JSON ──► Categorize Issues
       │
       ▼
   Aggregate
   Statistics
       │
       ▼
   Generate
   Reports
```

### 3. Issue Categorization

```
Raw Pyright Output
        │
        ▼
┌───────────────────────────────────┐
│  Pattern Matching                 │
│  - Check error message            │
│  - Check rule code                │
│  - Apply category patterns        │
└───────────┬───────────────────────┘
            │
            ▼
        Category
            │
            ├──► Optional (None checks)
            ├──► Import (module errors)
            ├──► Return Type (mismatches)
            ├──► Argument Type (parameter errors)
            ├──► List/Dict (collection typing)
            ├──► Async (coroutine issues)
            ├──► Type Annotation (missing hints)
            ├──► Assignment (variable types)
            └──► Other (uncategorized)
```

### 4. Report Generation Flow

```
                    Aggregated Data
                          │
          ┌───────────────┼───────────────┐
          │               │               │
          ▼               ▼               ▼
    ┌─────────┐    ┌──────────┐   ┌──────────┐
    │  Repos  │    │Categories│   │ Hotspots │
    └────┬────┘    └────┬─────┘   └────┬─────┘
         │              │              │
         └──────┬───────┴──────┬───────┘
                │              │
                ▼              ▼
         ┌────────────┐  ┌──────────┐
         │ Markdown   │  │   JSON   │
         │ Generator  │  │ Generator│
         └─────┬──────┘  └────┬─────┘
               │              │
               ▼              ▼
        ┌────────────┐  ┌──────────┐
        │ 4 MD files │  │ Stdout   │
        └────────────┘  └──────────┘
```

## Data Flow

### Input: Raw Pyright JSON

```json
{
  "summary": {
    "errorCount": 42,
    "warningCount": 3,
    "informationCount": 0
  },
  "generalDiagnostics": [
    {
      "file": "/path/to/file.py",
      "severity": "error",
      "message": "Cannot access member \"name\" for type \"User | None\"",
      "range": {"start": {"line": 10, "character": 15}},
      "rule": "reportOptionalMemberAccess"
    }
  ]
}
```

### Intermediate: Categorized Issues

```python
TypeIssue(
    file="src/api/routes/auth.py",
    line=11,
    column=16,
    severity="error",
    message="Cannot access member \"name\" for type \"User | None\"",
    rule="reportOptionalMemberAccess",
    category="Optional"  # ← Added by categorization
)
```

### Output: Aggregated Statistics

```json
{
  "aggregated_statistics": {
    "total_errors": 65,
    "total_warnings": 4,
    "by_category": {
      "Optional": 28,
      "Return Type": 15,
      "Import": 12,
      "Argument Type": 10
    },
    "by_repository": {
      "plexichat": {"errors": 42, "warnings": 3},
      "common-utils": {"errors": 15, "warnings": 1},
      "encryption": {"errors": 8, "warnings": 0}
    },
    "hotspot_files": [
      {
        "repository": "plexichat",
        "file": "src/api/routes/auth.py",
        "total_issues": 18,
        "by_category": {"Optional": 10, "Return Type": 8}
      }
    ]
  }
}
```

## Execution Modes

### Mode 1: Human-Readable Reports

```
User runs:
  python scripts/type_check_all.py
       │
       ▼
  Run pyright on each repo
       │
       ▼
  Parse and categorize
       │
       ▼
  Generate markdown reports
       │
       ▼
  Save to type_check_reports/
       │
       ▼
  Print console summary
```

### Mode 2: JSON Output

```
User runs:
  python scripts/type_check_json.py
       │
       ▼
  Run pyright on each repo
       │
       ▼
  Parse and categorize
       │
       ▼
  Build JSON structure
       │
       ▼
  Output to stdout
       │
       ▼
  Can be piped to file or consumed by CI/CD
```

### Mode 3: Convenience Wrappers

```
User runs:
  ./scripts/run_type_check.sh (or .ps1)
       │
       ├─► Check prerequisites
       │   - pyright installed?
       │   - Python available?
       │   - Venv activated?
       │
       ├─► Update submodules
       │
       ├─► Run type_check_all.py
       │
       └─► Display formatted results
```

## Integration Points

### CI/CD Pipeline Integration

```
┌─────────────────────────────────────────────┐
│           CI/CD Trigger                     │
│     (push, PR, schedule, manual)            │
└───────────────┬─────────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────────┐
│        Setup Environment                    │
│  - Install Node.js + pyright                │
│  - Install Python + dependencies            │
│  - Initialize submodules                    │
└───────────────┬─────────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────────┐
│   Run Type Check (JSON mode)               │
│   python scripts/type_check_json.py         │
└───────────────┬─────────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────────┐
│        Parse Results                        │
│  - Extract error/warning counts             │
│  - Compare against thresholds               │
│  - Generate PR comment                      │
└───────────────┬─────────────────────────────┘
                │
                ├──► Pass: Continue pipeline
                │
                └──► Fail: Block merge (optional)
```

## File Structure

```
plexichat/
│
├── scripts/
│   ├── type_check_all.py          ← Main script (markdown output)
│   ├── type_check_json.py         ← JSON variant
│   ├── example_json_consumer.py   ← Usage examples
│   ├── run_type_check.sh          ← Bash wrapper
│   ├── run_type_check.ps1         ← PowerShell wrapper
│   ├── report_schema.json         ← JSON schema
│   ├── gitlab-ci-typecheck.yml    ← GitLab CI config
│   ├── github-actions-typecheck.yml ← GitHub Actions config
│   ├── README.md                  ← Technical documentation
│   ├── QUICK_REFERENCE.md         ← One-page cheat sheet
│   └── ARCHITECTURE.md            ← This file
│
├── type_check_reports/ (generated)
│   ├── type_check_summary_consolidated.md
│   ├── type_check_report_plexichat.md
│   ├── type_check_report_common-utils.md
│   └── type_check_report_encryption.md
│
├── TYPE_CHECK_README.md           ← User guide
├── TYPE_CHECK_GUIDE.md            ← Detailed fixing guide
├── pyrightconfig.json             ← Type checking config
└── .gitignore                     ← Ignores type_check_reports/
```

## Extension Points

### Adding New Repositories

Edit `type_check_all.py`:

```python
def run(self):
    # ... existing code ...
    
    # Add your new repository
    self.check_my_new_repo()
    
def check_my_new_repo(self):
    repo_path = self.root_dir / "path" / "to" / "new_repo"
    config_file = self.root_dir / "pyrightconfig.json"
    output, _ = self.run_pyright(repo_path, config_file)
    report = self.parse_pyright_output(output, "new_repo", repo_path)
    self.reports.append(report)
```

### Adding New Categories

Edit `CATEGORY_PATTERNS`:

```python
CATEGORY_PATTERNS = {
    # ... existing categories ...
    "New Category": [
        "pattern1",
        "pattern2",
    ],
}
```

### Custom Report Formats

Create new method in orchestrator:

```python
def generate_html_report(self) -> str:
    # Generate HTML from self.reports
    pass

def generate_csv_export(self) -> str:
    # Generate CSV from self.reports
    pass
```

## Performance Characteristics

- **Execution Time**: ~30-60 seconds for all 3 repos (depends on codebase size)
- **Memory Usage**: ~200-500 MB (pyright process)
- **Parallel Execution**: Currently sequential (repos checked one at a time)
- **Optimization Opportunity**: Could parallelize repository checks

## Design Decisions

1. **Shared Config**: All repos use same `pyrightconfig.json`
   - Pro: Consistent rules across codebase
   - Con: Less flexibility per repo

2. **Automatic Categorization**: Issues categorized by pattern matching
   - Pro: Easy to understand and prioritize
   - Con: Some issues may be miscategorized

3. **Two Output Formats**: Markdown and JSON
   - Pro: Serves both human and machine consumers
   - Con: Duplicated logic

4. **Sequential Checking**: Repos checked one at a time
   - Pro: Simpler implementation, easier to debug
   - Con: Slower than parallel execution

5. **Submodule Support**: Checks both main repo and submodules
   - Pro: Complete coverage
   - Con: Requires submodule initialization
