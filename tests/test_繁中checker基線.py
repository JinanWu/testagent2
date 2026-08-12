"""驗證繁中checker嚴格baseline ratchet與fail-closed行為。"""
import importlib.util, json, sys
from pathlib import Path, PureWindowsPath
import pytest


def 載入checker():
    path = Path(__file__).resolve().parents[1] / "scripts" / "檢查繁中文檔.py"
    spec = importlib.util.spec_from_file_location("繁中checker", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_canonical跨平台_unicode與特殊字元無歧義(tmp_path):
    c = 載入checker(); root = tmp_path / "繁中代理"; root.mkdir()
    records = c.建立canonical問題(root, [c.問題(root / "é\nname.py", 3, "function_name", "訊息\n第二行")])
    assert records == [{"line": 3, "message": "訊息\n第二行", "path": "é\nname.py", "rule": "function_name"}]
    assert json.loads(c.編碼canonical問題(records)) == records
    assert c.正規化相對路徑(PureWindowsPath("C:/repo/root"), PureWindowsPath("C:/repo/root/a.py")) == "a.py"
    valid = tmp_path / "valid.json"; valid.write_text('[{"line":1,"message":"x","path":"nested/a.py","rule":"r"}]')
    assert c.讀取baseline(valid)[0]["path"] == "nested/a.py"


def test_root與baseline錯誤fail_closed(tmp_path):
    c = 載入checker(); missing = tmp_path / "missing"
    with pytest.raises(c.檢查設定錯誤): c.掃描問題(missing)
    file_root = tmp_path / "file"; file_root.write_text("x")
    with pytest.raises(c.檢查設定錯誤): c.掃描問題(file_root)
    empty = tmp_path / "empty"; empty.mkdir()
    with pytest.raises(c.檢查設定錯誤): c.掃描問題(empty)
    invalid = ["{}", "[1]", '[{"line":1,"message":"x","path":"a.py"}]',
      '[{"extra":1,"line":1,"message":"x","path":"a.py","rule":"r"}]',
      *[f'[{{"line":1,"message":"x","path":"{p}","rule":"r"}}]' for p in ("../a.py", "C:\\\\a.py", "\\\\\\\\server\\\\a.py", "a\\\\..\\\\b.py", "/a.py", "")],
      *[f'[{{"line":{line},"message":"x","path":"a.py","rule":"r"}}]' for line in ("true", "0", "-1")],
      '[{"line":1,"message":"","path":"a.py","rule":"r"}]', '[{"line":1,"message":"x","path":"a.py","rule":""}]',
      '[{"line":1,"message":"é","path":"a.py","rule":"r"}]',
      '[{"line":2,"message":"b","path":"b.py","rule":"r"},{"line":1,"message":"a","path":"a.py","rule":"r"}]',
      '[{"line":1,"message":"x","path":"a.py","rule":"r"},{"line":1,"message":"x","path":"a.py","rule":"r"}]']
    for payload in invalid:
        baseline = tmp_path / "baseline.json"; baseline.write_text(payload)
        with pytest.raises(c.檢查設定錯誤): c.判定問題集合(empty, [], baseline)


def test_exact_clean通過且drift_duplicate與bounded_delta失敗(tmp_path, capsys):
    c = 載入checker(); root = tmp_path / "繁中代理"; root.mkdir(); source = root / "a.py"; source.write_text("x=1")
    current = [c.問題(source, 1, "rule", "debt")]; baseline = tmp_path / "baseline.json"
    baseline.write_text(c.編碼canonical問題(c.建立canonical問題(root, current)))
    assert c.判定問題集合(root, current, baseline) == 0 and "既有問題1項" in capsys.readouterr().out
    assert c.判定問題集合(root, [], baseline) == 0 and "檢查通過" in capsys.readouterr().out
    for drift in ([*current, c.問題(source, 2, "rule", "new")], [c.問題(source, 1, "rule", "replacement")], [c.問題(source, 2, "rule", "debt")]):
        assert c.判定問題集合(root, drift, baseline) == 1; output = capsys.readouterr().out
        assert "新增" in output and "移除" in output
    with pytest.raises(c.檢查設定錯誤): c.判定問題集合(root, [], tmp_path / "missing.json")
    with pytest.raises(c.檢查設定錯誤, match="重複"): c.判定問題集合(root, current * 2, baseline)
    large = [c.問題(source, i, "rule", f"new-{i}") for i in range(2, 24)]
    assert c.判定問題集合(root, large, baseline) == 1; output = capsys.readouterr().out
    assert output.count("新增: ") == 20 and "delta僅顯示" in output


def test_執行檢查_exact通過_parse與空root_exit2(tmp_path, monkeypatch, capsys):
    c = 載入checker(); scripts, root = tmp_path / "scripts", tmp_path / "繁中代理"; scripts.mkdir(); root.mkdir()
    monkeypatch.setattr(c, "__file__", str(scripts / "檢查繁中文檔.py")); source = root / "a.py"; source.write_text("x=1")
    baseline = c.建立canonical問題(root, c.掃描問題(root)); (scripts / "繁中checker-baseline.json").write_text(c.編碼canonical問題(baseline))
    assert c.執行檢查() == 0 and "既有問題" in capsys.readouterr().out
    source.write_text("def broken(:"); assert c.執行檢查() == 2 and "掃描失敗" in capsys.readouterr().err
    source.unlink(); assert c.執行檢查() == 2 and "沒有Python source" in capsys.readouterr().err
