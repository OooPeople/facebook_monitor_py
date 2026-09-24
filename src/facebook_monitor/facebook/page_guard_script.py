"""Facebook page guard 的 bounded browser-side DOM probe。

職責：只收集可安全正規化的 marker context 與可見 feed 候選統計；產品分類、
重試與持久化語義留在 worker 層。
"""

from __future__ import annotations

from facebook_monitor.facebook.feed_dom_selectors import FEED_ROOT_SELECTORS_SCRIPT
from facebook_monitor.facebook.feed_dom_selectors import (
    POST_CONTAINER_CANDIDATE_SELECTORS_SCRIPT,
)
from facebook_monitor.facebook.feed_dom_selectors import POST_PERMALINK_ANCHOR_SELECTOR


FACEBOOK_PAGE_GUARD_STRUCTURE_SCRIPT = (
    r"""
({ titleMarkers, detailMarkers }) => {
  const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const normalize = (value) => clean(value).replace(/’/g, "'").toLowerCase();
  const boundedText = (element) => clean(element?.innerText || '').slice(0, 800);
  const includesMarker = (element, markers) => {
    const text = normalize(boundedText(element));
    return Boolean(text) && markers.some((marker) => text.includes(normalize(marker)));
  };
  const isRendered = (element) => {
    if (!element || element.closest('[hidden], [aria-hidden="true"]')) return false;
    for (let current = element; current; current = current.parentElement) {
      const style = window.getComputedStyle(current);
      if (
        style.display === 'none'
        || style.visibility === 'hidden'
        || style.visibility === 'collapse'
        || Number.parseFloat(style.opacity || '1') === 0
      ) return false;
    }
    return element.getClientRects().length > 0;
  };
  const headingSelector = 'h1, h2, [role="heading"]';
  const detailSelector = [
    'main p', 'main span', 'main div[dir="auto"]',
    '[role="main"] p', '[role="main"] span', '[role="main"] div[dir="auto"]',
    '[role="dialog"] p', '[role="dialog"] span', '[role="dialog"] div[dir="auto"]',
    '[role="alertdialog"] p', '[role="alertdialog"] span',
    '[role="alertdialog"] div[dir="auto"]',
    '[role="alert"] p', '[role="alert"] span', '[role="alert"] div[dir="auto"]',
  ].join(', ');
  const headingNodes = Array.from(document.querySelectorAll(headingSelector))
    .filter((element) => isRendered(element) && includesMarker(element, titleMarkers))
    .slice(0, 24);
  const detailNodes = Array.from(document.querySelectorAll(detailSelector))
    .filter((element) => {
      if (!isRendered(element)) return false;
      if (element.matches(headingSelector) || element.closest(headingSelector)) return false;
      if (element.querySelector(headingSelector)) return false;
      return includesMarker(element, detailMarkers);
    })
    .slice(0, 80);
  const feedRoots = """
    + FEED_ROOT_SELECTORS_SCRIPT
    + ";\n  const feedCandidateSelectors = "
    + POST_CONTAINER_CANDIDATE_SELECTORS_SCRIPT
    + """;
  const feedRootSelector = feedRoots.join(', ');
  const feedCandidateSelector = feedCandidateSelectors.join(', ');
  const postPermalinkSelector = """
    + repr(POST_PERMALINK_ANCHOR_SELECTOR)
    + r""";
  const feedCandidates = new Set();
  for (const root of document.querySelectorAll(feedRoots.join(', '))) {
    if (root.matches(feedCandidateSelector)) feedCandidates.add(root);
    for (const candidate of root.querySelectorAll(feedCandidateSelector)) {
      feedCandidates.add(candidate);
    }
  }
  const localTextWithinLimitCache = new Map();
  const localTextWithinLimit = (element) => {
    if (localTextWithinLimitCache.has(element)) {
      return localTextWithinLimitCache.get(element);
    }
    const withinLimit = String(element?.innerText || '').slice(0, 2401).length <= 2400;
    localTextWithinLimitCache.set(element, withinLimit);
    return withinLimit;
  };
  const insideFeedItem = (element) => {
    if (!element) return null;
    if (element.closest([
      'article',
      '[role="article"]',
      '[aria-posinset]',
      'div[data-pagelet*="FeedUnit"]',
      '[role="feed"] > div',
    ].join(', '))) return true;
    let current = element.parentElement;
    for (let depth = 0; current && depth <= 5; depth += 1) {
      if (current.matches([
        'main',
        '[role="main"]',
        '[role="dialog"]',
        '[role="alertdialog"]',
        '[role="alert"]',
      ].join(', '))) return false;
      const descendantFeedRoots = Array.from(current.querySelectorAll(feedRootSelector));
      if (localTextWithinLimit(current)) {
        const hasPermalinkOutsideDescendantFeed = Array.from(
          current.querySelectorAll(postPermalinkSelector),
        ).some((anchor) => (
          !descendantFeedRoots.some((root) => root.contains(anchor))
        ));
        if (hasPermalinkOutsideDescendantFeed) return true;
      }
      if (descendantFeedRoots.length > 0) return false;
      current = current.parentElement;
    }
    return false;
  };
  const localRelation = (heading, detail) => {
    if (!heading || !detail) return null;
    const headingAncestors = new Map();
    let current = heading;
    for (let depth = 0; current && depth <= 5; depth += 1) {
      headingAncestors.set(current, depth);
      current = current.parentElement;
    }
    current = detail;
    for (let depth = 0; current && depth <= 5; depth += 1) {
      const headingDepth = headingAncestors.get(current);
      if (headingDepth !== undefined && current !== document.body && current !== document.documentElement) {
        return headingDepth + depth <= 6 && localTextWithinLimit(current);
      }
      current = current.parentElement;
    }
    return false;
  };
  let selectedHeading = headingNodes[0] || null;
  let selectedDetail = detailNodes[0] || null;
  let selectedLocal = localRelation(selectedHeading, selectedDetail);
  for (const heading of headingNodes) {
    for (const detail of detailNodes) {
      if (!localRelation(heading, detail)) continue;
      selectedHeading = heading;
      selectedDetail = detail;
      selectedLocal = true;
      if (insideFeedItem(heading) === false && insideFeedItem(detail) === false) break;
    }
    if (
      selectedLocal === true
      && insideFeedItem(selectedHeading) === false
      && insideFeedItem(selectedDetail) === false
    ) break;
  }
  return {
    matchedHeadingText: boundedText(selectedHeading),
    matchedDetailText: boundedText(selectedDetail),
    headingInsideFeed: insideFeedItem(selectedHeading),
    detailInsideFeed: insideFeedItem(selectedDetail),
    headingDetailLocal: selectedLocal,
    visibleFeedCandidateCount: Array.from(feedCandidates).filter(isRendered).length,
  };
}
"""
)


__all__ = ["FACEBOOK_PAGE_GUARD_STRUCTURE_SCRIPT"]
