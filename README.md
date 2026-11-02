# Documentation claim auditor

An offline static auditor that compares documentation assertions with Python
source operations. Source files are parsed without execution. It uses no network,
writes no files, and collects no telemetry. Static evidence indicates a call
pattern, and does not prove runtime correctness or audio quality.

## Usage

```sh
python3 -m pitch_detection_claim_audit /path/to/project
pitch-detection-claim-audit /path/to/project --json
pitch-detection-claim-audit /path/to/project --verbose --ignore vendor --fail-on MEDIUM
python3 -m pitch_detection_claim_audit --version
```

The root document and Markdown documents under a documentation directory are
checked paragraph by paragraph. Fenced examples are excluded; inline code counts.
Matching ignores case, expands negative contractions, and normalizes hyphens and
whitespace. Negative spans are removed before affirmative matching.

Python files provide evidence within a single module. Imports and aliases are
resolved locally. Local function and method definitions suppress generic evidence.
Tests provide separate evidence; fixture, example, documentation, and symbolic
link paths do not provide production evidence. Invalid syntax provides no evidence.

```text
README.md
docs/**/*.md
fixtures/  examples/  docs/  tests/
test_*.py  *_test.py
librosa.piptrack / librosa.yin
crepe.predict / torchcrepe.predict / aubio.pitch
object.to_pitch
object.detect_pitch / object.estimate_pitch / object.get_pitch
object.pitch_detect / object.extract_pitch / object.pitch_track / object.track_pitch
```

Bare imports, unrelated audio operations, strings, and external commands do not
satisfy assertions. Module bindings are required for library-specific operations;
generic attribute operations accept any receiver. Generic imported functions
require a matching imported name. Nested module attribute chains are excluded.

Findings are emitted once per assertion family. Test evidence alone produces a
medium severity finding; unsupported assertions and contradicted denials produce
high severity findings. An affirmative assertion without source files still
produces a finding; a denial without source files passes.

```text
documented-pitch-detection-claim-false
documented-no-pitch-detection-claim-false
```

Exit status is zero for a passing threshold, two for findings meeting the chosen
threshold, and one for usage or input errors. The default threshold is HIGH;
MEDIUM and LOW are also accepted. A medium finding still appears in the report
when the default threshold returns zero. JSON includes a verdict, findings with
identifiers, severities and messages, scanned paths, and evidence flags. Ignore
options are repeatable root-relative path prefixes, matched on path boundaries.

## Development

```sh
python3 -m unittest discover
python3 -m build
```

Requires Python 3.9 or newer. Distributed under the MIT license.
