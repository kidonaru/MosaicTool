"""推論環境セットアップのコマンド組み立ての検証(uv は実行しない)"""
import sys
from pathlib import Path
from types import SimpleNamespace

from mosaic_tool.detect import runtime

UV = Path("/app/uv")
RUNTIME = Path("/app/runtime")


def test_venv_command_pins_python_version():
    cmd = runtime.venv_command(UV, RUNTIME)
    assert cmd[:3] == [str(UV), "venv", str(RUNTIME)]
    assert cmd[-2:] == ["--python", runtime.PYTHON_VERSION]


def test_venv_command_recreates_existing_venv():
    """既存の runtime/ があっても再セットアップできること"""
    assert "--clear" in runtime.venv_command(UV, RUNTIME)


def test_install_command_targets_the_venv():
    cmd = runtime.install_command(UV, RUNTIME, use_gpu=False)
    assert cmd[:3] == [str(UV), "pip", "install"]
    assert "--python" in cmd and str(RUNTIME) in cmd
    for package in runtime.PACKAGES:
        assert package in cmd


def test_cpu_install_has_no_cuda_index():
    cmd = runtime.install_command(UV, RUNTIME, use_gpu=False)
    assert "--extra-index-url" not in cmd


def test_gpu_install_adds_cuda_index(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(runtime, "gpu_compute_capability", lambda: 8.9)
    cmd = runtime.install_command(UV, RUNTIME, use_gpu=True)
    assert cmd[-2:] == ["--extra-index-url", runtime.TORCH_CUDA_INDEX_URL]


def test_gpu_install_uses_blackwell_cuda_index(monkeypatch):
    """RTX 50 系(sm_120)は cu121 のビルドにカーネルが無いため配布元を変える"""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(runtime, "gpu_compute_capability", lambda: 12.0)
    cmd = runtime.install_command(UV, RUNTIME, use_gpu=True)
    assert cmd[-2:] == ["--extra-index-url", runtime.TORCH_CUDA_INDEX_URL_BLACKWELL]


def test_cuda_index_falls_back_when_gpu_is_unknown(monkeypatch):
    monkeypatch.setattr(runtime, "gpu_compute_capability", lambda: None)
    assert runtime.cuda_index_url() == runtime.TORCH_CUDA_INDEX_URL


def test_compute_capability_takes_the_oldest_gpu(monkeypatch):
    """複数枚あるときは全部で動くよう最も古い世代に合わせる"""
    monkeypatch.setattr(
        runtime.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(stdout="12.0\n8.6\n"),
    )
    assert runtime.gpu_compute_capability() == 8.6


def test_compute_capability_is_none_without_nvidia_smi(monkeypatch):
    def raise_not_found(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(runtime.subprocess, "run", raise_not_found)
    assert runtime.gpu_compute_capability() is None


def test_compute_capability_ignores_unparsable_output(monkeypatch):
    monkeypatch.setattr(
        runtime.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(stdout="N/A\n"),
    )
    assert runtime.gpu_compute_capability() is None


def test_has_nvidia_gpu_uses_nvidia_smi(monkeypatch):
    monkeypatch.setattr(runtime.shutil, "which", lambda name: "C:/w/nvidia-smi.exe")
    assert runtime.has_nvidia_gpu()
    monkeypatch.setattr(runtime.shutil, "which", lambda name: None)
    assert not runtime.has_nvidia_gpu()


def test_gpu_install_has_no_cuda_index_on_macos(monkeypatch):
    # macOS に CUDA ビルドは存在しない(通常の wheel が MPS 対応済み)
    monkeypatch.setattr(sys, "platform", "darwin")
    cmd = runtime.install_command(UV, RUNTIME, use_gpu=True)
    assert "--extra-index-url" not in cmd


def test_gpu_choice_is_hidden_on_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert not runtime.supports_gpu_choice()
    monkeypatch.setattr(sys, "platform", "win32")
    assert runtime.supports_gpu_choice()


def test_resolve_device_uses_mps_for_auto_on_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert runtime.resolve_device("auto") == "mps"
    assert runtime.resolve_device("cpu") == "cpu"


def test_resolve_device_delegates_to_ultralytics_on_windows(monkeypatch):
    # Windows では空文字を渡して ultralytics の自動選択に任せる
    monkeypatch.setattr(sys, "platform", "win32")
    assert runtime.resolve_device("auto") == ""
    assert runtime.resolve_device("cpu") == "cpu"


def test_ensure_uv_executable_adds_exec_bit_on_posix(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    uv = tmp_path / "uv"
    uv.write_bytes(b"")
    uv.chmod(0o644)
    runtime.ensure_uv_executable(uv)
    assert uv.stat().st_mode & 0o111


def test_ensure_uv_executable_is_noop_on_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    uv = tmp_path / "uv.exe"
    uv.write_bytes(b"")
    # 存在しないファイルでも例外を出さないこと(Windows では何もしない)
    runtime.ensure_uv_executable(tmp_path / "missing.exe")
    runtime.ensure_uv_executable(uv)
