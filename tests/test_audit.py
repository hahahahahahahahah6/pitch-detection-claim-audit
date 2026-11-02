import ast
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from pitch_detection_claim_audit import phrases
from pitch_detection_claim_audit.audit import audit, claims, evidence, main, normalize

BASE = Path(__file__).resolve().parents[1]

# Independent fixed oracle: never read the external specification at test time.
EXPECTED_PITCH = (
    'pitch detection', 'detects pitch', 'detect pitch', 'pitch estimation',
    'estimates pitch', 'estimate pitch', 'pitch tracking', 'tracks pitch',
    'track pitch', 'fundamental frequency detection', 'detects fundamental frequency',
    'fundamental frequency estimation', 'f0 estimation', 'estimates f0',
    'f0 detection', 'pitch analysis', 'analyses pitch', 'analyzes pitch',
    'pitch extraction', 'extracts pitch', 'vocal pitch detection',
    'detects vocal pitch', 'musical pitch detection', 'detects musical pitch',
    'note detection', 'detects notes', 'note pitch detection',
    'pitch contour extraction', 'extracts pitch contours', 'pitch contour',
    'melodic pitch detection', 'detects melody pitch', 'monophonic pitch detection',
    'polyphonic pitch detection', 'pitch detection algorithm',
    'supports pitch detection', 'pitch detector', 'pitch estimation algorithm',
    'real-time pitch detection', 'automatic pitch detection', 'pitch measurement',
    'measures pitch', 'pitch recognition', 'recognizes pitch',
    'pitch identification', 'identifies pitch')
EXPECTED_NOPITCH = (
    'no pitch detection', 'does not detect pitch', 'without pitch detection',
    'no pitch estimation', 'does not estimate pitch', 'without pitch estimation',
    'no pitch tracking', 'does not track pitch', 'pitch detection not supported',
    'does not support pitch detection', 'cannot detect pitch', 'never detects pitch',
    'no fundamental frequency detection', 'does not detect fundamental frequency',
    'no f0 estimation', 'does not estimate f0', 'no pitch extraction',
    'does not extract pitch', 'no note detection', 'does not detect notes',
    'without pitch analysis', 'no pitch detector', 'pitch detection unavailable',
    'does not measure pitch')


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=BASE)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def put(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8')
        return target

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main([str(self.root)] + list(args))
        return code, out.getvalue(), err.getvalue()

    def test_phrase_oracle(self):
        tree = ast.parse(Path(phrases.__file__).read_text())
        parsed = {}
        for node in tree.body:
            if isinstance(node, ast.Assign):
                # Parse the literal underlying tuple(multiline.splitlines()).
                parsed[node.targets[0].id] = tuple(ast.literal_eval(node.value.args[0].func.value).splitlines())
        self.assertEqual(parsed['PITCH'], EXPECTED_PITCH)
        self.assertEqual(parsed['NOPITCH'], EXPECTED_NOPITCH)
        self.assertEqual(tuple(map(normalize, phrases.PITCH)), tuple(map(normalize, EXPECTED_PITCH)))
        self.assertEqual(tuple(map(normalize, phrases.NOPITCH)), tuple(map(normalize, EXPECTED_NOPITCH)))

    def test_every_phrase(self):
        for phrase in EXPECTED_PITCH:
            with self.subTest(phrase=phrase):
                self.assertEqual(claims(phrase), (True, False))
        for phrase in EXPECTED_NOPITCH:
            with self.subTest(phrase=phrase):
                self.assertEqual(claims(phrase), (False, True))

    def test_good(self):
        self.put('README.md', 'Pitch detection')
        self.put('app.py', 'import librosa\nlibrosa.yin(audio)')
        self.assertEqual(self.cli()[0], 0)
        self.assertEqual(audit(self.root)['verdict'], 'PASS')

    def test_bad_exact(self):
        self.put('README.md', 'Pitch detection')
        self.put('app.py', 'import librosa\nlibrosa.load(file)')
        code, out, _ = self.cli('--json')
        self.assertEqual(code, 2)
        result = json.loads(out)
        self.assertEqual(result['verdict'], 'FAIL')
        self.assertEqual(len(result['findings']), 1)
        self.assertEqual(result['findings'][0]['id'], 'documented-pitch-detection-claim-false')
        self.assertEqual(result['findings'][0]['severity'], 'HIGH')
        self.assertTrue(result['findings'][0]['message'])

    def test_test_only(self):
        self.put('README.md', 'Pitch detection')
        for path in ['tests/helper.py', 'test_app.py', 'app_test.py']:
            with self.subTest(path=path):
                p = self.put(path, 'x.to_pitch()')
                self.assertEqual(self.cli()[0], 0)
                self.assertEqual(audit(self.root)['findings'][0]['severity'], 'MEDIUM')
                self.assertEqual(self.cli('--fail-on', 'MEDIUM')[0], 2)
                p.unlink()

    def test_denial(self):
        self.put('README.md', 'does not detect pitch')
        self.put('app.py', 'x.to_pitch()')
        code, out, _ = self.cli('--json')
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)['findings'][0]['id'], 'documented-no-pitch-detection-claim-false')
        self.assertEqual(json.loads(out)['findings'][0]['severity'], 'HIGH')

    def test_no_claim(self):
        self.put('README.md', 'An audio tool')
        self.put('app.py', 'x.to_pitch()')
        self.assertEqual(audit(self.root)['verdict'], 'PASS')

    def test_no_source(self):
        self.put('README.md', 'pitch detection')
        self.assertEqual(self.cli()[0], 2)
        self.put('README.md', 'no pitch detection')
        self.assertEqual(self.cli()[0], 0)

    def test_fences_inline_normalization(self):
        for marker in ['```', '~~~', '   ````']:
            self.assertEqual(claims(marker + '\npitch detection\n' + marker), (False, False))
        self.assertEqual(claims('`pitch detection`'), (True, False))
        self.assertEqual(claims("DOESN'T detect-pitch"), (False, True))
        self.assertEqual(claims('pitch\n\ndetection'), (False, False))
        self.assertEqual(claims('pitch\n detection'), (True, False))
        self.assertEqual(claims('pitch detections'), (False, False))
        self.assertEqual(claims('no pitch detection\n\npitch detection'), (True, True))
        self.assertEqual(claims('```\npitch detection'), (False, False))
        self.assertEqual(claims('```\n~~~\npitch detection\n```'), (False, False))

    def test_evidence_routes(self):
        routes = [
            'import librosa\nlibrosa.piptrack(x)',
            'import librosa\nlibrosa.yin(x)',
            'import librosa as l\nl.yin(x)',
            'from librosa import piptrack\npiptrack(x)',
            'from librosa import yin as y\ny(x)',
            'import crepe\ncrepe.predict(x)',
            'import crepe as c\nc.predict(x)',
            'import torchcrepe\ntorchcrepe.predict(x)',
            'import torchcrepe as t\nt.predict(x)',
            'import aubio\naubio.pitch()',
            'import aubio as a\na.pitch()',
            'sound.to_pitch()',
            'import librosa\ndef yin(): pass\nlibrosa.yin(x)',
            'from librosa import yin\ndef yin(): pass\nyin(x)',
        ]
        for name in ['detect_pitch', 'estimate_pitch', 'get_pitch', 'pitch_detect', 'extract_pitch', 'pitch_track', 'track_pitch']:
            routes += ['obj.' + name + '()', 'from detector import ' + name + '\n' + name + '()',
                       'from detector import ' + name + ' as dp\ndp()']
        for source in routes:
            with self.subTest(source=source):
                self.assertTrue(evidence(source))

    def test_not_evidence(self):
        routes = [
            'import librosa', 'import crepe', 'import torchcrepe', 'import aubio', 'import parselmouth',
            'librosa.load(x)', 'soundfile.read(x)', 'wave.open(x).readframes(3)', 'torchaudio.load(x)',
            'mic.listen()', 'mic.record()', 'sounddevice.rec()', 'pyaudio.open().read(4)',
            'sounddevice.play(x)', 'engine.say(x)', 'model.transcribe(x)', 'r.recognize_google(x)', 'stt(x)',
            'vad.is_speech(x)', 'get_speech_timestamps(x)',
            'Pipeline.from_pretrained("pyannote/segmentation")',
            'pvporcupine.create()', 'openwakeword.Model().predict(x)', 'x.detect_keyword(x)',
            'speaker.verify_files(a,b)', 'encoder.embed_utterance(a)', 'x.verify_speaker(a,b)',
            'Pipeline.from_pretrained("pyannote/diarization")', 'x.diarize(a)',
            'noisereduce.reduce_noise(x)', 'df.enhance(x)', 'x.suppress_noise(x)', 'x.remove_noise(x)',
            'acoustid.fingerprint_file(x)', 'Dejavu().recognize(x)', 'x.fingerprint_audio(x)',
            'AudioTagging().inference(x)', 'yamnet(x)', 'x.detect_sound_event(x)',
            'classifier.predict(x)', 'pipeline("audio-classification")(x)',
            'demucs.separate(x)', 'Separator().separate(x)', 'x.separate_stems(x)',
            'librosa.feature.chroma_stft(x)', 'librosa.feature.mfcc(x)', 'x.spectral_centroid(x)',
            'librosa.feature.zero_crossing_rate(x)', 'librosa.beat.beat_track(x)',
            'librosa.onset.onset_detect(x)', 'librosa.feature.tempo(x)',
            'hashlib.sha256(data)', 'hashlib.md5(data)', 'filter(x)', 'equalize(x)',
            'mix(a,b)', 'normalize(x)', 'detect_language(x)', 'translate(x)',
            'librosa.piptrack(x)', 'wrong.yin(x)', 'wrong.predict(x)',
            'import numpy as librosa\nlibrosa.yin(x)',
            'import librosa\nlibrosa.predict(x)', 'import crepe\ncrepe.core.predict(x)',
            'import torchcrepe\ntorchcrepe.core.predict(x)',
            'from crepe import predict\npredict(x)', 'from aubio import pitch\npitch()',
            'subprocess.run(["crepe", "audio.wav"])', 'subprocess.run(["aubio", "pitch"])',
            's = "librosa.yin(x)"', 'import librosa\nlibrosa.yin',
            'def broken(: pass', 'detect_pitch(x)', 'to_pitch(x)',
            'def detect_pitch(x): pass\nobj.detect_pitch(x)',
            'class A:\n def to_pitch(self): pass\nx.to_pitch()',
            'from d import get_pitch as gp\ndef get_pitch(): pass\ngp()',
            'from d import track_pitch as tp\ndef tp(): pass\ntp()',
            'def f():\n import librosa\nlibrosa.yin(x)',
        ]
        for source in routes:
            with self.subTest(source=source):
                self.assertFalse(evidence(source))

    def test_all_generic_shadows(self):
        for name in ['detect_pitch', 'estimate_pitch', 'get_pitch', 'pitch_detect', 'extract_pitch', 'pitch_track', 'track_pitch', 'to_pitch']:
            self.assertFalse(evidence('class C:\n def ' + name + '(self): pass\nx.' + name + '()'))

    def test_split_modules(self):
        self.put('README.md', 'pitch detection')
        self.put('a.py', 'import librosa as l')
        self.put('b.py', 'l.yin(x)')
        self.assertEqual(self.cli()[0], 2)

    def test_excluded(self):
        self.put('README.md', 'pitch detection')
        for path in ['fixtures/a.py', 'docs/a.py', 'examples/a.py']:
            self.put(path, 'obj.to_pitch()')
        self.assertEqual(audit(self.root)['findings'][0]['severity'], 'HIGH')
        target = self.put('fixtures/target.py', 'obj.to_pitch()')
        (self.root / 'link.py').symlink_to(target)
        (self.root / 'linked').symlink_to(target.parent, target_is_directory=True)
        self.assertEqual(self.cli()[0], 2)
        self.put('fixtures/claims.md', 'pitch detection')

    def test_docs_selection(self):
        self.put('other.md', 'pitch detection')
        self.assertEqual(self.cli()[0], 0)
        self.put('nested/docs/guide.md', 'pitch detection')
        self.assertEqual(self.cli()[0], 2)

    def test_ignore_and_verbose(self):
        self.put('README.md', 'pitch detection')
        self.put('vendor/app.py', 'x.to_pitch()')
        self.assertEqual(self.cli()[0], 0)
        self.assertEqual(self.cli('--ignore', 'vendor', '--verbose')[0], 2)
        self.assertEqual(self.cli('--ignore', 'ven')[0], 0)
        self.assertEqual(self.cli('--ignore', 'vendor', '--ignore', 'README.md')[0], 0)

    def test_usage_errors(self):
        for args in [[], ['/nonexistent'], [str(self.root), '--fail-on', 'BOGUS'], [str(self.root), '--unknown']]:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                try:
                    code = main(args)
                except SystemExit as exc:
                    code = exc.code
            self.assertEqual(code, 1)

    def test_both_families_once(self):
        self.put('README.md', 'pitch detection\n\npitch estimation\n\nno pitch detection')
        self.assertEqual(len(audit(self.root)['findings']), 1)
        self.put('app.py', 'x.to_pitch()')
        self.assertEqual(len(audit(self.root)['findings']), 1)

    def test_module_entry_version(self):
        proc = subprocess.run([sys.executable, '-m', 'pitch_detection_claim_audit', '--version'], capture_output=True, text=True, cwd=BASE)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), '0.1.0')

    def test_console_declaration(self):
        self.assertIn('pitch-detection-claim-audit = "pitch_detection_claim_audit.audit:main"', (BASE / 'pyproject.toml').read_text())

    def test_self_audit(self):
        self.assertEqual(claims((BASE / 'README.md').read_text()), (False, False))
        self.assertEqual(audit(BASE)['verdict'], 'PASS')


if __name__ == '__main__':
    unittest.main()
