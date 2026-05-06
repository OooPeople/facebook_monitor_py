"""Facebook browser capture helpers。

職責：提供 headed browser capture 時共用的 page snapshot 與 route 選取邏輯。
此模組不負責啟動 Playwright，也不寫入資料庫。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from facebook_monitor.facebook.route_detection import CapturedGroupPostsRoute
from facebook_monitor.facebook.route_detection import RouteDetectionError
from facebook_monitor.facebook.route_detection import detect_group_posts_route


@dataclass(frozen=True)
class BrowserPageSnapshot:
    """保存 capture 當下 Playwright 看得到的頁面資訊。"""

    page_index: int
    url: str
    title: str
    candidate_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class CaptureSelection:
    """保存從多個瀏覽器頁面中選出的 capture target。"""

    route: CapturedGroupPostsRoute
    snapshot: BrowserPageSnapshot
    source_url: str
    valid_count: int


def snapshot_browser_pages(pages: list[Any]) -> list[BrowserPageSnapshot]:
    """擷取所有開啟頁面的 URL 與 title，避免只讀到起始 tab。"""

    snapshots: list[BrowserPageSnapshot] = []
    for index, browser_page in enumerate(pages, start=1):
        if getattr(browser_page, "is_closed", lambda: False)():
            continue
        url = str(getattr(browser_page, "url", "") or "")
        try:
            title = str(browser_page.title() or "")
        except Exception:
            title = ""
        snapshots.append(
            BrowserPageSnapshot(
                page_index=index,
                url=url,
                title=title,
                candidate_urls=extract_page_candidate_urls(browser_page),
            )
        )
    return snapshots


def extract_page_candidate_urls(browser_page: Any) -> tuple[str, ...]:
    """從頁面 DOM 擷取可能代表目前 route 的 URL 候選。"""

    try:
        payload = browser_page.evaluate(
            """() => {
                const urls = [];
                const pushUrl = (value) => {
                    if (typeof value === 'string' && value.trim()) {
                        urls.push(value.trim());
                    }
                };

                pushUrl(window.location.href);
                pushUrl(document.location.href);
                pushUrl(document.querySelector('link[rel="canonical"]')?.href);
                pushUrl(document.querySelector('meta[property="og:url"]')?.content);

                const activeSelectors = [
                    'a[aria-current="page"][href*="/groups/"]',
                    'a[aria-selected="true"][href*="/groups/"]',
                    '[role="tab"][aria-selected="true"][href*="/groups/"]'
                ];
                for (const selector of activeSelectors) {
                    pushUrl(document.querySelector(selector)?.href);
                }

                return Array.from(new Set(urls));
            }"""
        )
    except Exception:
        return ()
    return tuple(str(item) for item in payload if item)


def iter_snapshot_urls(snapshot: BrowserPageSnapshot) -> tuple[str, ...]:
    """依優先順序回傳 snapshot 可用於 route detection 的 URL。"""

    urls = [snapshot.url, *snapshot.candidate_urls]
    return tuple(dict.fromkeys(url for url in urls if url))


def select_capture_route(snapshots: list[BrowserPageSnapshot]) -> CaptureSelection:
    """從所有頁面中選出可保存的 group posts route。"""

    valid_candidates: list[CaptureSelection] = []
    seen_page_group_keys: set[tuple[int, str]] = set()
    for snapshot in snapshots:
        for candidate_url in iter_snapshot_urls(snapshot):
            try:
                route = detect_group_posts_route(candidate_url, page_title=snapshot.title)
            except RouteDetectionError:
                continue
            page_group_key = (snapshot.page_index, route.group_id)
            if page_group_key in seen_page_group_keys:
                continue
            seen_page_group_keys.add(page_group_key)
            valid_candidates.append(
                CaptureSelection(
                    route=route,
                    snapshot=snapshot,
                    source_url=candidate_url,
                    valid_count=0,
                )
            )

    if not valid_candidates:
        urls = "\n".join(format_snapshot_debug_line(item) for item in snapshots)
        raise RouteDetectionError(
            "找不到可保存的 Facebook group feed。Playwright 目前看得到的頁面：\n"
            + (urls or "- (no open pages)")
        )

    selected = valid_candidates[-1]
    return CaptureSelection(
        route=selected.route,
        snapshot=selected.snapshot,
        source_url=selected.source_url,
        valid_count=len(valid_candidates),
    )


def format_snapshot_debug_line(snapshot: BrowserPageSnapshot) -> str:
    """格式化 capture 失敗時要輸出的頁面 URL 診斷資訊。"""

    candidates = ", ".join(snapshot.candidate_urls) if snapshot.candidate_urls else "(none)"
    return f"- page {snapshot.page_index}: {snapshot.url} candidates=[{candidates}]"


def get_start_page(context: Any) -> Any:
    """取得啟動後可用的第一個頁面，避免額外產生 about:blank 分頁。"""

    for browser_page in context.pages:
        if not getattr(browser_page, "is_closed", lambda: False)():
            return browser_page
    return context.new_page()
