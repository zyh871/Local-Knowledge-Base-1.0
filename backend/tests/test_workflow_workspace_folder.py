"""工作区绑定本地文件夹的测试。

两条底线：

  * **绑定必须校验目录真实存在** —— 否则用户得到一个「看起来绑好了、
    点开却是空的」工作区，比直接报错难排查得多
  * **引用文件只能来自绑定目录内** —— 不加这条限制，这个端点就等于
    「任意路径读取服务器文件」，而它原本只是为了让用户不必重传已有文件
"""

from __future__ import annotations

import os

import pytest

from app.workflow.local_fs import (
    ensure_local_dir,
    list_dir,
    list_roots,
    open_in_file_manager,
)


# ---------------------------------------------------------------------------
#  纯逻辑
# ---------------------------------------------------------------------------


class TestEnsureLocalDir:
    def test_accepts_existing_dir(self, tmp_path):
        assert ensure_local_dir(str(tmp_path)) == os.path.abspath(str(tmp_path))

    def test_rejects_missing(self, tmp_path):
        from app.core.exceptions import ValidationError

        with pytest.raises(ValidationError, match="不存在"):
            ensure_local_dir(str(tmp_path / "nope"))

    def test_rejects_file(self, tmp_path):
        from app.core.exceptions import ValidationError

        target = tmp_path / "a.txt"
        target.write_text("x", encoding="utf-8")
        with pytest.raises(ValidationError, match="不是一个文件夹"):
            ensure_local_dir(str(target))

    def test_rejects_empty(self):
        from app.core.exceptions import ValidationError

        with pytest.raises(ValidationError):
            ensure_local_dir("   ")

    def test_expands_user_home(self):
        """用户很自然会输 `~/Documents`；不展开会得到一个不存在的相对路径，
        然后报「目录不存在」—— 用户会以为是自己错了。"""
        resolved = ensure_local_dir("~")
        assert resolved == os.path.abspath(os.path.expanduser("~"))

    def test_strips_quotes(self, tmp_path):
        """从资源管理器复制路径时会带引号。"""
        assert ensure_local_dir(f'"{tmp_path}"') == os.path.abspath(str(tmp_path))


class TestListDir:
    def test_lists_dirs_first_then_files(self, tmp_path):
        (tmp_path / "b.txt").write_text("1", encoding="utf-8")
        (tmp_path / "a_dir").mkdir()
        (tmp_path / "z.txt").write_text("2", encoding="utf-8")

        data = list_dir(str(tmp_path))
        names = [e["name"] for e in data["entries"]]
        assert names[0] == "a_dir"  # 目录优先
        assert set(names[1:]) == {"b.txt", "z.txt"}

    def test_entry_metadata(self, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "f.txt").write_text("hello", encoding="utf-8")
        data = list_dir(str(tmp_path))
        by_name = {e["name"]: e for e in data["entries"]}
        assert by_name["sub"]["is_dir"] is True
        assert by_name["sub"]["size"] == 0
        assert by_name["f.txt"]["is_dir"] is False
        assert by_name["f.txt"]["size"] == 5
        assert by_name["f.txt"]["path"].endswith("f.txt")

    def test_hidden_entries_skipped(self, tmp_path):
        (tmp_path / ".hidden").mkdir()
        (tmp_path / "visible").mkdir()
        names = [e["name"] for e in list_dir(str(tmp_path))["entries"]]
        assert names == ["visible"]

    def test_root_has_no_parent(self):
        """到根时不给「上一级」—— 否则点了没反应，用户以为界面坏了。

        直接测路径运算而不是真去 `list_dir("C:\\\\")`：枚举整个根目录又慢又可能报权限错，
        而这里要验证的只是「根的父目录是不是自己」。
        """
        from app.workflow.local_fs import _parent_of

        root = os.path.abspath(os.sep)  # Windows → "C:\\"，其余 → "/"
        assert _parent_of(root) is None

    def test_drive_relative_parent_never_returned(self):
        """回归：曾经返回过 `"C:"`（盘符相对路径，不是真实目录）。

        它是 dirname("C:") 的产物，会被按当前工作目录解析 ——
        前端拿它去请求「上一级」，列出来的会是一个莫名其妙的目录。
        """
        from app.workflow.local_fs import _parent_of

        parent = _parent_of(os.path.abspath(os.sep))
        assert parent is None
        # 任何返回的父目录都必须是绝对路径
        assert _parent_of(os.path.join(os.path.abspath(os.sep), "某目录")) is not None

    def test_parent_points_to_ancestor(self, tmp_path):
        child = tmp_path / "child"
        child.mkdir()
        data = list_dir(str(child))
        assert data["parent"] == os.path.abspath(str(tmp_path))

    def test_truncates_large_dir(self, tmp_path):
        for i in range(20):
            (tmp_path / f"f{i:03d}.txt").write_text("x", encoding="utf-8")
        data = list_dir(str(tmp_path), max_entries=5)
        assert len(data["entries"]) == 5
        assert data["truncated"] is True

    def test_missing_dir_raises(self, tmp_path):
        from app.core.exceptions import ValidationError

        with pytest.raises(ValidationError, match="不存在"):
            list_dir(str(tmp_path / "nope"))

    def test_never_returns_file_contents(self, tmp_path):
        """★ 只列元信息，绝不返回内容 —— 这是这层能力的安全边界。"""
        (tmp_path / "secret.txt").write_text("绝密内容", encoding="utf-8")
        data = list_dir(str(tmp_path))
        assert "绝密内容" not in str(data)


class TestListRoots:
    def test_returns_at_least_one_root(self):
        roots = list_roots()
        assert roots
        assert all({"name", "path"} <= set(r) for r in roots)

    def test_home_first(self):
        """从「主目录」开始比从 `/` 或盘符根开始顺手得多。"""
        assert list_roots()[0]["name"] == "主目录"


class TestOpenInFileManager:
    def test_invokes_platform_command(self, tmp_path, monkeypatch):
        import app.workflow.local_fs as mod

        calls: list[list[str]] = []
        monkeypatch.setattr(
            mod.subprocess, "Popen", lambda args: calls.append(list(args))
        )
        opened = open_in_file_manager(str(tmp_path))
        assert opened == os.path.abspath(str(tmp_path))
        assert len(calls) == 1

    def test_missing_dir_raises(self, tmp_path, monkeypatch):
        from app.core.exceptions import ValidationError

        monkeypatch.setattr("app.workflow.local_fs.subprocess.Popen", lambda *_: None)
        with pytest.raises(ValidationError):
            open_in_file_manager(str(tmp_path / "nope"))


# ---------------------------------------------------------------------------
#  接口
# ---------------------------------------------------------------------------


@pytest.fixture
def wf_env(tmp_path, monkeypatch):
    from app.core.config import clear_settings_cache
    from app.repositories import workflow_repo as repo

    monkeypatch.setenv("WORKFLOW_DB_PATH", str(tmp_path / "workflow.db"))
    monkeypatch.setenv("WORKFLOW_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    clear_settings_cache()
    repo.reset_connection()
    yield tmp_path
    repo.reset_connection()
    clear_settings_cache()


@pytest.fixture
def folder(tmp_path):
    """一个「用户的本机文件夹」，里面放一个待引用的文件。"""
    target = tmp_path / "我的资料"
    target.mkdir()
    (target / "已有资料.txt").write_text("这是我原本就有的资料", encoding="utf-8")
    (target / "子目录").mkdir()
    return target


class TestWorkspaceBinding:
    def test_create_with_local_dir(self, client, auth_headers, wf_env, folder):
        resp = client.post(
            "/workflow/workspaces",
            json={"name": "绑定测试", "local_dir": str(folder)},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["local_dir"] == os.path.abspath(str(folder))

    def test_create_without_local_dir_is_allowed(self, client, auth_headers, wf_env):
        resp = client.post(
            "/workflow/workspaces", json={"name": "不绑定"}, headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["local_dir"] == ""

    def test_create_with_missing_dir_rejected(self, client, auth_headers, wf_env, tmp_path):
        """★ 绑一个不存在的目录必须当场报错，而不是"看起来绑好了"。"""
        resp = client.post(
            "/workflow/workspaces",
            json={"name": "坏绑定", "local_dir": str(tmp_path / "不存在")},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_bind_and_unbind(self, client, auth_headers, wf_env, folder):
        workspace = client.post(
            "/workflow/workspaces", json={"name": "后绑定"}, headers=auth_headers
        ).json()

        bound = client.put(
            f"/workflow/workspaces/{workspace['id']}/folder",
            json={"local_dir": str(folder)},
            headers=auth_headers,
        )
        assert bound.status_code == 200
        assert bound.json()["local_dir"] == os.path.abspath(str(folder))

        unbound = client.put(
            f"/workflow/workspaces/{workspace['id']}/folder",
            json={"local_dir": ""},
            headers=auth_headers,
        )
        assert unbound.status_code == 200
        assert unbound.json()["local_dir"] == ""

    def test_local_dir_survives_listing(self, client, auth_headers, wf_env, folder):
        client.post(
            "/workflow/workspaces",
            json={"name": "列表里要能看见", "local_dir": str(folder)},
            headers=auth_headers,
        )
        items = client.get("/workflow/workspaces", headers=auth_headers).json()["workspaces"]
        target = next(w for w in items if w["name"] == "列表里要能看见")
        assert target["local_dir"] == os.path.abspath(str(folder))

    def test_other_users_cannot_rebind(
        self, client, auth_headers, wf_env, folder
    ):
        from app.repositories import workflow_repo as repo

        workspace = client.post(
            "/workflow/workspaces", json={"name": "别人的"}, headers=auth_headers
        ).json()
        with repo._lock, repo._connect() as conn:  # noqa: SLF001
            conn.execute(
                "UPDATE workspaces SET owner = ? WHERE id = ?",
                ("someone-else", workspace["id"]),
            )
        resp = client.put(
            f"/workflow/workspaces/{workspace['id']}/folder",
            json={"local_dir": str(folder)},
            headers=auth_headers,
        )
        assert resp.status_code == 403


class TestBrowseFs:
    def test_lists_given_path_with_roots(self, client, auth_headers, wf_env, folder):
        resp = client.get(
            "/workflow/fs/list", params={"path": str(folder)}, headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["path"] == os.path.abspath(str(folder))
        assert data["roots"]  # 起始位置列表，前端不必知道平台差异
        names = [e["name"] for e in data["entries"]]
        assert "子目录" in names and "已有资料.txt" in names

    def test_empty_path_gives_a_starting_point(self, client, auth_headers, wf_env):
        data = client.get("/workflow/fs/list", headers=auth_headers).json()
        assert data["path"]  # 不该报错，也不该返回空
        assert data["entries"] is not None

    def test_missing_path_is_400(self, client, auth_headers, wf_env, tmp_path):
        resp = client.get(
            "/workflow/fs/list",
            params={"path": str(tmp_path / "nope")},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_disabled_by_config(self, client, auth_headers, wf_env, folder, monkeypatch):
        from app.core.config import clear_settings_cache
        from app.repositories import workflow_repo as repo

        monkeypatch.setenv("WORKFLOW_FS_BROWSE", "false")
        clear_settings_cache()
        repo.reset_connection()
        resp = client.get(
            "/workflow/fs/list", params={"path": str(folder)}, headers=auth_headers
        )
        assert resp.status_code == 400
        assert "禁用" in resp.json()["error"]["message"]


class TestImportLocalFilesBatch:
    """批量引用：一个文件的问题不该让其余文件也加不进来。"""

    def _run(self, client, auth_headers, folder):
        workspace = client.post(
            "/workflow/workspaces",
            json={"name": f"批量-{os.getpid()}", "local_dir": str(folder)},
            headers=auth_headers,
        ).json()
        return client.post(
            "/workflow/runs",
            json={"workspace_id": workspace["id"], "raw_input": "一起加上"},
            headers=auth_headers,
        ).json()

    def test_imports_multiple(self, client, auth_headers, wf_env, folder):
        (folder / "b.md").write_text("# b", encoding="utf-8")
        run = self._run(client, auth_headers, folder)
        resp = client.post(
            f"/workflow/runs/{run['id']}/files/import-batch",
            json={"paths": [str(folder / "已有资料.txt"), str(folder / "b.md")]},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert {f["filename"] for f in body["files"]} == {"已有资料.txt", "b.md"}
        assert body["failed"] == []

    def test_partial_failure_does_not_block_others(
        self, client, auth_headers, wf_env, folder, tmp_path
    ):
        """★ 一个不支持的类型不该让另外的文件也加不进来，且要**点名**是谁没进来。"""
        (folder / "archive.zip").write_text("PK", encoding="utf-8")
        run = self._run(client, auth_headers, folder)
        resp = client.post(
            f"/workflow/runs/{run['id']}/files/import-batch",
            json={
                "paths": [
                    str(folder / "已有资料.txt"),
                    str(folder / "archive.zip"),
                ]
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert [f["filename"] for f in body["files"]] == ["已有资料.txt"]
        assert len(body["failed"]) == 1
        assert body["failed"][0]["name"] == "archive.zip"
        assert "不支持" in body["failed"][0]["reason"]

    def test_rejects_paths_outside_bound_folder(
        self, client, auth_headers, wf_env, folder, tmp_path
    ):
        """★ 安全边界同样适用于批量入口。"""
        outside = tmp_path / "外部.txt"
        outside.write_text("不该被读到", encoding="utf-8")
        run = self._run(client, auth_headers, folder)
        resp = client.post(
            f"/workflow/runs/{run['id']}/files/import-batch",
            json={"paths": [str(folder / "已有资料.txt"), str(outside)]},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert [f["filename"] for f in body["files"]] == ["已有资料.txt"]
        assert "工作区文件夹" in body["failed"][0]["reason"]

    def test_all_invalid_is_400(self, client, auth_headers, wf_env, folder, tmp_path):
        outside = tmp_path / "外部.txt"
        outside.write_text("x", encoding="utf-8")
        run = self._run(client, auth_headers, folder)
        resp = client.post(
            f"/workflow/runs/{run['id']}/files/import-batch",
            json={"paths": [str(outside)]},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_empty_paths_is_rejected(self, client, auth_headers, wf_env, folder):
        run = self._run(client, auth_headers, folder)
        resp = client.post(
            f"/workflow/runs/{run['id']}/files/import-batch",
            json={"paths": []},
            headers=auth_headers,
        )
        assert resp.status_code == 422  # schema 层就挡掉

    def test_unbound_workspace_refused(self, client, auth_headers, wf_env, folder):
        workspace = client.post(
            "/workflow/workspaces", json={"name": "批量未绑定"}, headers=auth_headers
        ).json()
        run = client.post(
            "/workflow/runs",
            json={"workspace_id": workspace["id"], "raw_input": "x"},
            headers=auth_headers,
        ).json()
        resp = client.post(
            f"/workflow/runs/{run['id']}/files/import-batch",
            json={"paths": [str(folder / "已有资料.txt")]},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "没有绑定" in resp.json()["error"]["message"]


class TestRunDetailFolderAndContext:
    """任务详情要能回答两个用户必问的问题：文件夹里有什么、实际进去多少字。"""

    def _run(self, client, auth_headers, folder, name="详情"):
        workspace = client.post(
            "/workflow/workspaces",
            json={"name": f"{name}-{os.getpid()}", "local_dir": str(folder)},
            headers=auth_headers,
        ).json()
        return client.post(
            "/workflow/runs",
            json={"workspace_id": workspace["id"], "raw_input": "整理这个文件夹"},
            headers=auth_headers,
        ).json()

    def test_folder_summary_present(self, client, auth_headers, wf_env, folder):
        run = self._run(client, auth_headers, folder)
        detail = client.get(f"/workflow/runs/{run['id']}", headers=auth_headers).json()
        summary = detail["folder"]
        assert summary is not None
        assert summary["total_files"] >= 1
        names = [f["name"] for f in summary["files"]]
        assert "已有资料.txt" in names
        # ★ 摘要里不能有内容
        assert "这是我原本就有的资料" not in str(summary)

    def test_folder_is_none_when_unbound(self, client, auth_headers, wf_env):
        workspace = client.post(
            "/workflow/workspaces", json={"name": "详情未绑定"}, headers=auth_headers
        ).json()
        run = client.post(
            "/workflow/runs",
            json={"workspace_id": workspace["id"], "raw_input": "x"},
            headers=auth_headers,
        ).json()
        detail = client.get(f"/workflow/runs/{run['id']}", headers=auth_headers).json()
        assert detail["folder"] is None

    def test_file_context_none_without_files(self, client, auth_headers, wf_env, folder):
        run = self._run(client, auth_headers, folder, name="无文件")
        assert run["file_context"] is None

    def test_file_context_reports_injected_chars(self, client, auth_headers, wf_env, folder):
        """★ 「全部加入 ≠ 全部进上下文」必须能被看见。"""
        (folder / "big.md").write_text("内容" * 20000, encoding="utf-8")  # 约 4 万字
        run = self._run(client, auth_headers, folder, name="注入预览")
        client.post(
            f"/workflow/runs/{run['id']}/files/import",
            json={"path": str(folder / "big.md")},
            headers=auth_headers,
        )
        detail = client.get(f"/workflow/runs/{run['id']}", headers=auth_headers).json()
        ctx = detail["file_context"]
        assert ctx is not None
        assert ctx["budget_chars"] > 0
        assert ctx["total_chars"] > ctx["budget_chars"]  # 确实超预算了
        assert 0 < ctx["injected_chars"] <= ctx["total_chars"]
        note = ctx["notes"][0]
        assert note["injected_chars"] < note["chars"]
        assert note["mode"] == "retrieved"


class TestImportLocalFile:
    def _run(self, client, auth_headers, folder):
        workspace = client.post(
            "/workflow/workspaces",
            json={"name": f"导入测试-{os.getpid()}", "local_dir": str(folder)},
            headers=auth_headers,
        ).json()
        return client.post(
            "/workflow/runs",
            json={"workspace_id": workspace["id"], "raw_input": "用上我的资料"},
            headers=auth_headers,
        ).json()

    def test_imports_file_from_bound_folder(self, client, auth_headers, wf_env, folder):
        run = self._run(client, auth_headers, folder)
        resp = client.post(
            f"/workflow/runs/{run['id']}/files/import",
            json={"path": str(folder / "已有资料.txt")},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        files = resp.json()["files"]
        assert len(files) == 1
        assert files[0]["filename"] == "已有资料.txt"
        assert files[0]["kind"] == "input"
        assert files[0]["parsed_chars"]  # 真的走了解析

        # 内容确实复制进来了（不依赖原文件后续是否被移动）
        download = client.get(
            f"/workflow/files/{files[0]['id']}/download", headers=auth_headers
        )
        assert "这是我原本就有的资料" in download.content.decode("utf-8")

    def test_imports_html_from_bound_folder(
        self, client, auth_headers, wf_env, folder
    ):
        """★ 用户的实际场景：把已有 HTML 页面引用进来当参考。

        `.html` 此前不在白名单里，选择器又照常把它列出来 ——
        用户点了被拒，于是合理地怀疑「程序访问不了我的本地文件」。
        """
        (folder / "mome.html").write_text(
            '<!DOCTYPE html><html><body><h1>我的页面</h1></body></html>',
            encoding="utf-8",
        )
        run = self._run(client, auth_headers, folder)
        resp = client.post(
            f"/workflow/runs/{run['id']}/files/import",
            json={"path": str(folder / "mome.html")},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        record = resp.json()["files"][0]
        assert record["filename"] == "mome.html"
        assert record["parsed_chars"]

        download = client.get(
            f"/workflow/files/{record['id']}/download", headers=auth_headers
        )
        assert "我的页面" in download.content.decode("utf-8")

    def test_rejects_file_outside_bound_folder(
        self, client, auth_headers, wf_env, folder, tmp_path
    ):
        """★ 安全边界：只能引用绑定目录内的文件。

        不加这条限制，这个端点就等于「任意路径读取服务器文件」。
        """
        outside = tmp_path / "外部机密.txt"
        outside.write_text("不该被读到", encoding="utf-8")

        run = self._run(client, auth_headers, folder)
        resp = client.post(
            f"/workflow/runs/{run['id']}/files/import",
            json={"path": str(outside)},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "工作区文件夹" in resp.json()["error"]["message"]

    def test_rejects_path_traversal(self, client, auth_headers, wf_env, folder):
        run = self._run(client, auth_headers, folder)
        resp = client.post(
            f"/workflow/runs/{run['id']}/files/import",
            json={"path": str(folder / ".." / "逃逸.txt")},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_rejects_missing_file(self, client, auth_headers, wf_env, folder):
        run = self._run(client, auth_headers, folder)
        resp = client.post(
            f"/workflow/runs/{run['id']}/files/import",
            json={"path": str(folder / "没有这个.txt")},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_rejects_when_workspace_has_no_folder(
        self, client, auth_headers, wf_env, folder
    ):
        """★ 未绑定文件夹时必须拒绝。

        这条曾经是个**后门**：当时的写法是「绑定了才校验包含性」，
        于是未绑定的工作区反倒能引用任意路径的服务器文件。
        没有绑定就没有可引用的来源 —— 拒绝是唯一正确的语义。
        """
        workspace = client.post(
            "/workflow/workspaces", json={"name": "没有绑定"}, headers=auth_headers
        ).json()
        run = client.post(
            "/workflow/runs",
            json={"workspace_id": workspace["id"], "raw_input": "x"},
            headers=auth_headers,
        ).json()
        resp = client.post(
            f"/workflow/runs/{run['id']}/files/import",
            json={"path": str(folder / "已有资料.txt")},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "没有绑定" in resp.json()["error"]["message"]

    def test_unbound_workspace_cannot_read_arbitrary_path(
        self, client, auth_headers, wf_env, tmp_path
    ):
        """未绑定时连工作区外的文件也读不到（回归测试）。"""
        secret = tmp_path / "任意路径.txt"
        secret.write_text("不该被读到", encoding="utf-8")
        workspace = client.post(
            "/workflow/workspaces", json={"name": "无绑定2"}, headers=auth_headers
        ).json()
        run = client.post(
            "/workflow/runs",
            json={"workspace_id": workspace["id"], "raw_input": "x"},
            headers=auth_headers,
        ).json()
        resp = client.post(
            f"/workflow/runs/{run['id']}/files/import",
            json={"path": str(secret)},
            headers=auth_headers,
        )
        assert resp.status_code == 400


class TestOpenWorkspaceFolder:
    def test_opens_bound_folder(self, client, auth_headers, wf_env, folder, monkeypatch):
        import app.workflow.local_fs as mod

        calls: list[list[str]] = []
        monkeypatch.setattr(mod.subprocess, "Popen", lambda args: calls.append(list(args)))

        workspace = client.post(
            "/workflow/workspaces",
            json={"name": "打开测试", "local_dir": str(folder)},
            headers=auth_headers,
        ).json()
        resp = client.post(
            f"/workflow/workspaces/{workspace['id']}/open", headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["path"] == os.path.abspath(str(folder))
        assert len(calls) == 1

    def test_opens_managed_dir_when_unbound(
        self, client, auth_headers, wf_env, monkeypatch
    ):
        """没绑本地文件夹时打开服务端分配的目录 —— 临时文件与产物确实躺在那里。"""
        import app.workflow.local_fs as mod

        calls: list[list[str]] = []
        monkeypatch.setattr(mod.subprocess, "Popen", lambda args: calls.append(list(args)))

        workspace = client.post(
            "/workflow/workspaces", json={"name": "未绑定"}, headers=auth_headers
        ).json()
        resp = client.post(
            f"/workflow/workspaces/{workspace['id']}/open", headers=auth_headers
        )
        assert resp.status_code == 200
        assert os.path.isdir(resp.json()["path"])
