"""Facebook feed DOM script fragment.

職責：保存 `POST_LIKE_ITEMS_SCRIPT` 的單一責任 JavaScript 片段。
"""

FEED_DOM_DIAGNOSTICS_SCRIPT = '''            const buildDiagnosticHref = (value) => {
                const url = normalizeFacebookUrl(value);
                if (!url) return String(value || "").slice(0, 180);
                const diagnosticUrl = new URL(`${url.origin}${url.pathname}`);
                const keepParams = [
                    "story_fbid",
                    "multi_permalinks",
                    "set",
                    "id",
                    "group_id",
                    "group",
                    "idorvanity",
                    "comment_id",
                    "reply_comment_id",
                ];
                for (const key of keepParams) {
                    const values = url.searchParams.getAll(key);
                    for (const item of values) {
                        diagnosticUrl.searchParams.append(key, item);
                    }
                }
                return diagnosticUrl.toString();
            };
            const collectDiagnosticAttributeNames = (element) => {
                if (!(element instanceof HTMLElement)) return [];
                return Array.from(element.attributes)
                    .map((attribute) => attribute.name)
                    .filter((name) => (
                        name === "role" ||
                        name === "tabindex" ||
                        name === "aria-label" ||
                        name === "aria-hidden" ||
                        name === "attributionsrc" ||
                        name.startsWith("data-")
                    ))
                    .slice(0, 16);
            };
            const buildDiagnosticDomPath = (element, root) => {
                if (!(element instanceof HTMLElement)) return "";
                const parts = [];
                let current = element;
                while (current instanceof HTMLElement && current !== root && parts.length < 5) {
                    const tag = current.tagName.toLowerCase();
                    const role = current.getAttribute("role");
                    const attrs = [];
                    if (role) attrs.push(`role=${role}`);
                    if (current.hasAttribute("aria-label")) attrs.push("aria-label");
                    const dataKeys = collectDiagnosticAttributeNames(current)
                        .filter((name) => name.startsWith("data-"))
                        .slice(0, 2);
                    attrs.push(...dataKeys);
                    parts.unshift(attrs.length ? `${tag}[${attrs.join(",")}]` : tag);
                    current = current.parentElement;
                }
                if (current === root) parts.unshift("container");
                return parts.join(" > ");
            };
            const collectWarmupAnchorDetails = (anchor, container, href, relativeTop) => {
                const rect = anchor.getBoundingClientRect();
                const rawHref = anchor.getAttribute("href") || "";
                const innerText = normalizeText(anchor.innerText || "");
                const textContent = normalizeText(anchor.textContent || "");
                const ariaLabel = normalizeText(anchor.getAttribute("aria-label") || "");
                const parentText = normalizeText(
                    anchor.parentElement?.innerText ||
                    anchor.parentElement?.textContent ||
                    ""
                );
                return {
                    rawHref: buildDiagnosticHref(rawHref || href),
                    resolvedHref: buildDiagnosticHref(href),
                    role: anchor.getAttribute("role") || "",
                    tabIndex: anchor.getAttribute("tabindex") || "",
                    ariaHidden: anchor.getAttribute("aria-hidden") || "",
                    ariaLabel: ariaLabel.slice(0, 120),
                    innerText: innerText.slice(0, 120),
                    textContent: textContent.slice(0, 120),
                    parentText: parentText.slice(0, 160),
                    attributeNames: collectDiagnosticAttributeNames(anchor),
                    domPath: buildDiagnosticDomPath(anchor, container),
                    rect: {
                        relativeTop: Math.round(relativeTop),
                        width: Math.round(rect.width),
                        height: Math.round(rect.height),
                    },
                };
            };
            const classifyDiagnosticHref = (href, expectedGroupId = "") => {
                const url = normalizeFacebookUrl(href);
                if (!url) return "non_facebook";
                const details = extractCanonicalPermalinkFromHref(href, expectedGroupId);
                if (details.permalink) return `canonical:${details.source || "unknown"}`;
                const pathname = url.pathname.replace(/\\/+$/, "");
                if (isCommentPermalinkHref(href)) return "comment_permalink";
                if (isLikelyUserProfileHref(href)) return "profile";
                if (/^\\/hashtag\\//i.test(pathname)) return "hashtag";
                if (/^\\/photo(?:\\.php)?$/i.test(pathname)) return "photo_without_post_id";
                if (/^\\/groups\\/[^/?#]+$/i.test(pathname)) return "group_home";
                if (/^\\/l\\.php$/i.test(pathname)) return "external_redirect";
                if (/^\\/groups\\/[^/?#]+(?:\\/.*)?$/i.test(pathname)) return "group_other";
                return "facebook_other";
            };
            const collectLinkDiagnostics = (container, expectedGroupId = "") => {
                if (!(container instanceof HTMLElement)) {
                    return { total: 0, kindCounts: {}, samples: [] };
                }
                const kindCounts = {};
                const samples = [];
                const seen = new Set();
                const anchors = Array.from(container.querySelectorAll('a[href]'))
                    .filter((anchor) => anchor instanceof HTMLAnchorElement);
                for (const anchor of anchors) {
                    const href = String(anchor.href || anchor.getAttribute("href") || "").trim();
                    if (!href) continue;
                    const kind = classifyDiagnosticHref(href, expectedGroupId);
                    kindCounts[kind] = (kindCounts[kind] || 0) + 1;
                    const diagnosticHref = buildDiagnosticHref(href);
                    const signature = `${kind}||${diagnosticHref}`;
                    if (samples.length >= 8 || seen.has(signature)) continue;
                    seen.add(signature);
                    const canonicalDetails = extractCanonicalPermalinkFromHref(href, expectedGroupId);
                    samples.push({
                        kind,
                        href: diagnosticHref,
                        text: normalizeText(
                            anchor.innerText ||
                            anchor.textContent ||
                            anchor.getAttribute("aria-label") ||
                            ""
                        ).slice(0, 80),
                        hasAttributionSrc: anchor.hasAttribute("attributionsrc"),
                        canonicalSource: canonicalDetails.source || "",
                    });
                }
                return { total: anchors.length, kindCounts, samples };
            };
            const isElementInContainerUpperRegion = (element, container) => {
                if (!(element instanceof HTMLElement) || !(container instanceof HTMLElement)) return false;
                const containerRect = container.getBoundingClientRect();
                const elementRect = element.getBoundingClientRect();
                const relativeTop = elementRect.top - containerRect.top;
                const upperLimit = Math.max(210, containerRect.height * 0.46);
                return relativeTop >= -16 && relativeTop <= upperLimit;
            };
            const collectAnchorsFromScope = (scopeNode, selector = "a[href]", options = {}) => {
                if (!(scopeNode instanceof HTMLElement)) return [];
                const { excludeUserProfile = false, maxItems = Number.POSITIVE_INFINITY } = options;
                const anchors = [];
                const seen = new Set();
                const pushAnchor = (anchor) => {
                    if (!(anchor instanceof HTMLAnchorElement)) return;
                    const href = String(anchor.href || anchor.getAttribute("href") || "").trim();
                    if (!href || seen.has(href)) return;
                    if (excludeUserProfile && isLikelyUserProfileHref(href)) return;
                    seen.add(href);
                    anchors.push(anchor);
                };
                if (scopeNode instanceof HTMLAnchorElement && scopeNode.matches(selector)) {
                    pushAnchor(scopeNode);
                }
                for (const anchor of scopeNode.querySelectorAll(selector)) {
                    pushAnchor(anchor);
                    if (anchors.length >= maxItems) break;
                }
                return anchors;
            };
            const collectCanonicalPermalinkCandidates = (scopeNode, expectedGroupId = "", options = {}) => {
                if (!(scopeNode instanceof HTMLElement)) return [];
                const { upperRegionOnly = false } = options;
                const candidates = [];
                const seen = new Set();
                for (const anchor of collectAnchorsFromScope(scopeNode, postPermalinkAnchors)) {
                    if (upperRegionOnly && !isElementInContainerUpperRegion(anchor, scopeNode)) continue;
                    const href = anchor.href || anchor.getAttribute("href") || "";
                    const details = extractCanonicalPermalinkFromHref(href, expectedGroupId);
                    if (!details.permalink || seen.has(details.permalink)) continue;
                    seen.add(details.permalink);
                    candidates.push({
                        anchor,
                        href,
                        permalink: details.permalink,
                        source: details.source,
                        isCommentLink: isCommentPermalinkHref(href),
                    });
                }
                candidates.sort((a, b) => {
                    if (a.isCommentLink !== b.isCommentLink) return a.isCommentLink ? 1 : -1;
                    const sourceDiff = getPermalinkSourcePriority(a.source) - getPermalinkSourcePriority(b.source);
                    if (sourceDiff !== 0) return sourceDiff;
                    const topDiff = Math.round(a.anchor.getBoundingClientRect().top) - Math.round(b.anchor.getBoundingClientRect().top);
                    if (topDiff !== 0) return topDiff;
                    return a.href.length - b.href.length;
                });
                return candidates;
            };
            const collectPermalinkWarmupAnchors = (container, expectedGroupId = getCurrentGroupId(), limit = 4) => {
                if (!(container instanceof HTMLElement)) return [];
                const anchors = [];
                const seen = new Set();
                const containerRect = container.getBoundingClientRect();
                const upperRegionThreshold = Math.max(180, Math.round(containerRect.height * 0.38));
                for (const anchor of collectAnchorsFromScope(container, 'a[role="link"], a[href]')) {
                    if (!(anchor instanceof HTMLAnchorElement)) continue;
                    if (!isVisible(anchor)) continue;
                    const href = String(anchor.href || anchor.getAttribute("href") || "").trim();
                    const text = normalizeText(
                        anchor.innerText ||
                        anchor.textContent ||
                        anchor.getAttribute("aria-label") ||
                        ""
                    );
                    const relativeTop = anchor.getBoundingClientRect().top - containerRect.top;
                    const canonicalDetails = extractCanonicalPermalinkFromHref(href, expectedGroupId);
                    const likelyTimestamp = isLikelyTimestampAnchorText(text);
                    const likelyHeaderTimestamp = isLikelyHeaderTimestampWarmupAnchor(
                        anchor,
                        href,
                        text,
                        relativeTop,
                        expectedGroupId
                    );
                    const hasAttributionSrc = anchor.hasAttribute("attributionsrc");
                    if (relativeTop < -16 || relativeTop > upperRegionThreshold) continue;
                    if (isLikelyUserProfileHref(href)) continue;
                    if (
                        !canonicalDetails.permalink &&
                        isLikelyWarmupUtilityHref(href, expectedGroupId) &&
                        !likelyTimestamp &&
                        !likelyHeaderTimestamp &&
                        !hasAttributionSrc
                    ) {
                        continue;
                    }
                    const signature = `${href}||${text}||${Math.round(relativeTop)}`;
                    if (seen.has(signature)) continue;
                    seen.add(signature);
                    anchors.push({
                        anchor,
                        href,
                        text,
                        relativeTop,
                        canonicalDetails,
                        likelyTimestamp,
                        likelyHeaderTimestamp,
                        hasAttributionSrc,
                    });
                }
                anchors.sort((a, b) => {
                    if (Boolean(a.canonicalDetails.permalink) !== Boolean(b.canonicalDetails.permalink)) {
                        return a.canonicalDetails.permalink ? -1 : 1;
                    }
                    if (a.likelyTimestamp !== b.likelyTimestamp) {
                        return a.likelyTimestamp ? -1 : 1;
                    }
                    if (a.likelyHeaderTimestamp !== b.likelyHeaderTimestamp) {
                        return a.likelyHeaderTimestamp ? -1 : 1;
                    }
                    if (a.hasAttributionSrc !== b.hasAttributionSrc) {
                        return a.hasAttributionSrc ? -1 : 1;
                    }
                    return Math.round(a.relativeTop) - Math.round(b.relativeTop);
                });
                return anchors.slice(0, limit);
            };
            const collectPermalinkWarmupDiagnostics = (container, expectedGroupId = getCurrentGroupId(), limit = 8) => {
                if (!(container instanceof HTMLElement)) {
                    return { total: 0, acceptedCount: 0, rejectedReasonCounts: {}, samples: [] };
                }
                const containerRect = container.getBoundingClientRect();
                const upperRegionThreshold = Math.max(180, Math.round(containerRect.height * 0.38));
                const reasonCounts = {};
                const samples = [];
                let total = 0;
                let acceptedCount = 0;
                const pushReason = (reason) => {
                    reasonCounts[reason] = (reasonCounts[reason] || 0) + 1;
                };
                for (const anchor of collectAnchorsFromScope(container, 'a[role="link"], a[href]', { maxItems: 24 })) {
                    if (!(anchor instanceof HTMLAnchorElement)) continue;
                    total += 1;
                    const href = String(anchor.href || anchor.getAttribute("href") || "").trim();
                    const text = normalizeText(
                        anchor.innerText ||
                        anchor.textContent ||
                        anchor.getAttribute("aria-label") ||
                        ""
                    );
                    const relativeTop = Math.round(anchor.getBoundingClientRect().top - containerRect.top);
                    const canonicalDetails = extractCanonicalPermalinkFromHref(href, expectedGroupId);
                    const likelyTimestamp = isLikelyTimestampAnchorText(text);
                    const likelyHeaderTimestamp = isLikelyHeaderTimestampWarmupAnchor(
                        anchor,
                        href,
                        text,
                        relativeTop,
                        expectedGroupId
                    );
                    const hasAttributionSrc = anchor.hasAttribute("attributionsrc");
                    const isVisibleAnchor = isVisible(anchor);
                    const isUserProfile = isLikelyUserProfileHref(href);
                    const isUtilityHref = isLikelyWarmupUtilityHref(href, expectedGroupId);
                    let reason = "accepted";
                    if (!isVisibleAnchor) {
                        reason = "not_visible";
                    } else if (relativeTop < -16) {
                        reason = "above_upper_region";
                    } else if (relativeTop > upperRegionThreshold) {
                        reason = "below_upper_region";
                    } else if (isUserProfile) {
                        reason = "user_profile";
                    } else if (
                        !canonicalDetails.permalink &&
                        isUtilityHref &&
                        !likelyTimestamp &&
                        !likelyHeaderTimestamp &&
                        !hasAttributionSrc
                    ) {
                        reason = "utility_href_without_timestamp_or_attribution";
                    }
                    if (reason === "accepted") {
                        acceptedCount += 1;
                    } else {
                        pushReason(reason);
                    }
                    if (samples.length < limit) {
                        samples.push({
                            reason,
                            href: buildDiagnosticHref(href),
                            kind: classifyDiagnosticHref(href, expectedGroupId),
                            text: text.slice(0, 120),
                            relativeTop,
                            upperRegionThreshold,
                            likelyTimestamp,
                            likelyHeaderTimestamp,
                            hasAttributionSrc,
                            canonicalSource: canonicalDetails.source || "unavailable",
                            hasCanonicalPermalink: Boolean(canonicalDetails.permalink),
                            anchorDetails: collectWarmupAnchorDetails(
                                anchor,
                                container,
                                href,
                                relativeTop
                            ),
                        });
                    }
                }
                return {
                    total,
                    acceptedCount,
                    rejectedReasonCounts: reasonCounts,
                    samples,
                };
            };
'''

__all__ = ["FEED_DOM_DIAGNOSTICS_SCRIPT"]
