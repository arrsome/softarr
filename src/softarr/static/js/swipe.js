/**
 * swipe.js -- Touch swipe gesture handler for staging queue cards.
 *
 * Attaches to elements with the class `.swipe-card` and `data-release-id`.
 * Right swipe -> Approve the release.
 * Left swipe  -> Reject the release.
 *
 * CSS classes `.swipe-right` and `.swipe-left` (defined in styles.css) are
 * applied during gesture animation. On action completion the card fades out
 * and is removed from the DOM; on failure it snaps back.
 */

(function () {
    'use strict';

    var SWIPE_THRESHOLD = 80;   // Minimum horizontal distance (px) to trigger action
    var SNAP_DURATION   = 200;  // Transition duration for snap-back (ms)

    /**
     * Attach swipe listeners to all `.swipe-card` elements currently in the DOM.
     */
    function attachSwipeListeners() {
        var cards = document.querySelectorAll('.swipe-card');
        cards.forEach(function (card) {
            if (card.dataset.swipeAttached) return;
            card.dataset.swipeAttached = 'true';

            var startX = 0;
            var startY = 0;
            var currentX = 0;
            var overlay = null;

            function createOverlay(label, isApprove) {
                var el = document.createElement('div');
                el.className = 'swipe-overlay';
                el.style.cssText = [
                    'position:absolute',
                    'inset:0',
                    'display:flex',
                    'align-items:center',
                    'padding:0 1.5rem',
                    'font-size:0.75rem',
                    'font-weight:600',
                    'letter-spacing:0.05em',
                    'pointer-events:none',
                    'border-radius:inherit',
                    'transition:opacity 0.15s',
                    isApprove
                        ? 'color:#4ade80;justify-content:flex-end;'
                        : 'color:#f87171;justify-content:flex-start;',
                ].join(';');
                el.textContent = label;
                // Ensure card has position context for overlay
                var pos = window.getComputedStyle(card).position;
                if (pos === 'static') card.style.position = 'relative';
                card.appendChild(el);
                return el;
            }

            function removeOverlay() {
                if (overlay) {
                    overlay.remove();
                    overlay = null;
                }
            }

            function snapBack() {
                card.style.transition = 'transform ' + SNAP_DURATION + 'ms ease-out';
                card.style.transform = 'translateX(0)';
                card.classList.remove('swipe-right', 'swipe-left');
                removeOverlay();
                setTimeout(function () {
                    card.style.transition = '';
                }, SNAP_DURATION);
            }

            function dismissCard() {
                card.style.transition = 'opacity 0.25s ease-out, transform 0.25s ease-out';
                card.style.opacity = '0';
                card.style.transform = currentX > 0
                    ? 'translateX(100%)'
                    : 'translateX(-100%)';
                setTimeout(function () {
                    card.remove();
                }, 300);
            }

            card.addEventListener('touchstart', function (e) {
                startX = e.touches[0].clientX;
                startY = e.touches[0].clientY;
                currentX = 0;
                card.style.transition = 'none';
            }, { passive: true });

            card.addEventListener('touchmove', function (e) {
                var dx = e.touches[0].clientX - startX;
                var dy = e.touches[0].clientY - startY;

                // If primarily vertical, don't intercept scroll
                if (Math.abs(dy) > Math.abs(dx) && Math.abs(dx) < 10) return;

                currentX = dx;
                card.style.transform = 'translateX(' + dx + 'px)';

                var isApprove = dx > 0;
                var magnitude = Math.min(Math.abs(dx) / SWIPE_THRESHOLD, 1);

                if (magnitude > 0.3) {
                    card.classList.toggle('swipe-right', isApprove);
                    card.classList.toggle('swipe-left', !isApprove);
                    if (!overlay) {
                        overlay = createOverlay(
                            isApprove ? 'APPROVE' : 'REJECT',
                            isApprove
                        );
                    } else {
                        overlay.textContent = isApprove ? 'APPROVE' : 'REJECT';
                        overlay.style.color = isApprove ? '#4ade80' : '#f87171';
                        overlay.style.justifyContent = isApprove ? 'flex-end' : 'flex-start';
                    }
                    overlay.style.opacity = String(magnitude);
                } else {
                    card.classList.remove('swipe-right', 'swipe-left');
                    removeOverlay();
                }
            }, { passive: true });

            card.addEventListener('touchend', function () {
                if (Math.abs(currentX) < SWIPE_THRESHOLD) {
                    snapBack();
                    return;
                }

                var releaseId = card.dataset.releaseId;
                if (!releaseId) {
                    snapBack();
                    return;
                }

                var isApprove = currentX > 0;
                var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
                var endpoint = isApprove
                    ? '/api/v1/staging/approve/' + releaseId
                    : '/api/v1/staging/transition/' + releaseId;
                var body = isApprove ? null : JSON.stringify({ target_state: 'rejected' });
                var headers = { 'X-CSRF-Token': csrf };
                if (!isApprove) headers['Content-Type'] = 'application/json';

                fetch(endpoint, { method: 'POST', headers: headers, body: body })
                    .then(function (resp) {
                        if (resp.ok) {
                            dismissCard();
                        } else {
                            snapBack();
                        }
                    })
                    .catch(function () {
                        snapBack();
                    });
            });
        });
    }

    // Attach on initial load
    document.addEventListener('DOMContentLoaded', attachSwipeListeners);

    // Re-attach after HTMX DOM updates
    document.body.addEventListener('htmx:afterSwap', attachSwipeListeners);
})();
