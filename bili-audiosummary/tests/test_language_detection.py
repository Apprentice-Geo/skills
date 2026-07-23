import sys
import types

import numpy as np

from scripts import language_detection


def test_detect_language_uses_highest_score_and_warns_on_low_confidence(
    workspace_tmp_path, monkeypatch, capsys
) -> None:
    model_dir = workspace_tmp_path / "language-model"
    model_dir.mkdir()
    for filename in language_detection.LANGUAGE_ID_REQUIRED_FILES:
        (model_dir / filename).write_bytes(b"x")
    monkeypatch.setattr(language_detection, "LANGUAGE_ID_MODEL_DIR", model_dir)
    monkeypatch.setattr(
        language_detection,
        "_speech_sample",
        lambda _path: np.zeros(16_000, dtype=np.float32),
    )

    class Score:
        def reshape(self, *_args):
            return self

        def __getitem__(self, _index):
            return self

        def exp(self):
            return self

        def item(self):
            return 0.6

    class Classifier:
        @classmethod
        def from_hparams(cls, **_kwargs):
            return cls()

        def classify_batch(self, _signal):
            return object(), Score(), object(), ["ja: Japanese"]

    torch = types.ModuleType("torch")
    torch.from_numpy = lambda value: types.SimpleNamespace(
        unsqueeze=lambda _axis: value
    )
    classifiers = types.ModuleType("speechbrain.inference.classifiers")
    classifiers.EncoderClassifier = Classifier
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "speechbrain", types.ModuleType("speechbrain"))
    monkeypatch.setitem(
        sys.modules, "speechbrain.inference", types.ModuleType("speechbrain.inference")
    )
    monkeypatch.setitem(sys.modules, "speechbrain.inference.classifiers", classifiers)

    result = language_detection.detect_language(workspace_tmp_path / "audio.wav")

    assert result == language_detection.LanguageDetection("ja", 0.6)
    assert "continuing with the highest-scoring language" in capsys.readouterr().err
