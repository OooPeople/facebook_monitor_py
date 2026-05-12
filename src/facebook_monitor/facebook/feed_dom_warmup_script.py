"""Facebook feed DOM script fragment.

職責：保存 `POST_LIKE_ITEMS_SCRIPT` 的單一責任 JavaScript 片段。
"""

FEED_DOM_WARMUP_SCRIPT = r'''            const dispatchPermalinkWarmupEvents = (anchor) => {
                if (!(anchor instanceof HTMLElement)) return;
                const eventInit = {
                    bubbles: true,
                    cancelable: true,
                    composed: true,
                    view: window,
                };
                try {
                    anchor.dispatchEvent(new MouseEvent("mouseenter", eventInit));
                    anchor.dispatchEvent(new MouseEvent("mouseover", eventInit));
                    anchor.dispatchEvent(new MouseEvent("mousemove", eventInit));
                } catch (error) {
                    // 忽略事件建立失敗，改走 focus fallback。
                }
                try {
                    anchor.dispatchEvent(new PointerEvent("pointerenter", eventInit));
                    anchor.dispatchEvent(new PointerEvent("pointerover", eventInit));
                } catch (error) {
                    // 某些執行環境未必支援 PointerEvent。
                }
                try {
                    anchor.focus({ preventScroll: true });
                } catch (error) {
                    try {
                        anchor.focus();
                    } catch (focusError) {
                        // 忽略 focus 失敗。
                    }
                }
            };
            const buildPermalinkWarmupState = ({
                warmupAttempted = false,
                warmupResolved = false,
                warmupCandidateCount = 0,
            } = {}) => ({
                warmupAttempted: Boolean(warmupAttempted),
                warmupResolved: Boolean(warmupResolved),
                warmupCandidateCount: Number(warmupCandidateCount) || 0,
            });
            const warmPermalinkAnchors = async (container) => {
                if (!(container instanceof HTMLElement)) return buildPermalinkWarmupState();
                const expectedGroupId = getCurrentGroupId();
                const warmupAnchors = collectPermalinkWarmupAnchors(container, expectedGroupId);
                if (!warmupAnchors.length) return buildPermalinkWarmupState();
                let warmupAttempted = false;
                for (const candidate of warmupAnchors) {
                    if (candidate.canonicalDetails.permalink) {
                        return buildPermalinkWarmupState({
                            warmupAttempted,
                            warmupResolved: true,
                            warmupCandidateCount: warmupAnchors.length,
                        });
                    }
                    warmupAttempted = true;
                    dispatchPermalinkWarmupEvents(candidate.anchor);
                    await sleep(90);
                    const refreshedHref = candidate.anchor.href || candidate.anchor.getAttribute("href") || "";
                    const refreshedDetails = extractCanonicalPermalinkFromHref(refreshedHref, expectedGroupId);
                    if (refreshedDetails.permalink) {
                        return buildPermalinkWarmupState({
                            warmupAttempted,
                            warmupResolved: true,
                            warmupCandidateCount: warmupAnchors.length,
                        });
                    }
                }
                return buildPermalinkWarmupState({
                    warmupAttempted,
                    warmupResolved: false,
                    warmupCandidateCount: warmupAnchors.length,
                });
            };
'''

__all__ = ["FEED_DOM_WARMUP_SCRIPT"]
