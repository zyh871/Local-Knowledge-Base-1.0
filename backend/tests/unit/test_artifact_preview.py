"""交付物预览接口的行为契约。

客户端是 WebView2，`a[download]` 那条路走不通，所以「看文件」必须由接口喂内容。
这里锁住三种分支：文本、图片、二进制，以及大文件的截断标记。
"""

from app.services.workflow_service import WorkflowService


def _svc(tmp_path, filename: str, content: bytes):
    """构造一个只带 resolve_file_path 的服务实例（不连库）。"""
    path = tmp_path / filename
    path.write_bytes(content)

    svc = WorkflowService.__new__(WorkflowService)
    svc.resolve_file_path = lambda **kwargs: (str(path), filename)  # type: ignore[method-assign]
    return svc


def test_preview_text(tmp_path):
    svc = _svc(tmp_path, "报告.md", "# 标题\n\n中文内容".encode())
    data = svc.preview_file(file_id="f1", username="u")
    assert data["kind"] == "text"
    assert data["language"] == "markdown"
    assert "中文内容" in data["text"]
    assert data["truncated"] is False


def test_preview_image(tmp_path):
    svc = _svc(tmp_path, "shot.png", b"\x89PNG\r\n\x1a\n fake")
    data = svc.preview_file(file_id="f2", username="u")
    assert data["kind"] == "image"
    assert data["media_type"] == "image/png"


def test_preview_binary(tmp_path):
    svc = _svc(tmp_path, "包.zip", b"PK\x03\x04")
    data = svc.preview_file(file_id="f3", username="u")
    assert data["kind"] == "binary"
    assert "下载" in data["message"]


def test_preview_truncates_large_text(tmp_path):
    limit = WorkflowService._PREVIEW_MAX_BYTES
    svc = _svc(tmp_path, "大日志.log", b"x" * (limit + 1024))
    data = svc.preview_file(file_id="f4", username="u")
    assert data["kind"] == "text"
    assert data["truncated"] is True
    assert len(data["text"]) == limit


def test_file_bytes_returns_media_type(tmp_path):
    svc = _svc(tmp_path, "图.jpg", b"\xff\xd8\xff")
    data, name, media = svc.file_bytes(file_id="f5", username="u")
    assert name == "图.jpg"
    assert media == "image/jpeg"
    assert data.startswith(b"\xff\xd8")
