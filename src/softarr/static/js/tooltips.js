/**
 * Softarr tooltip system.
 *
 * Usage: add a data-tooltip="..." attribute to any element.
 * The tooltip appears on hover (desktop) or tap (mobile).
 * Optionally add data-tooltip-title="..." for a bold heading line.
 *
 * No external libraries required -- pure vanilla JS.
 */
(function () {
    'use strict';

    var tip = null;     // The floating tooltip element
    var current = null; // The element currently showing a tooltip

    function createTip() {
        var el = document.createElement('div');
        el.className = 'arr-tooltip';
        el.setAttribute('role', 'tooltip');
        el.setAttribute('aria-hidden', 'true');
        document.body.appendChild(el);
        return el;
    }

    function show(trigger) {
        var text = trigger.getAttribute('data-tooltip');
        var title = trigger.getAttribute('data-tooltip-title');
        if (!text) return;

        if (!tip) tip = createTip();

        tip.innerHTML = '';
        if (title) {
            var h = document.createElement('strong');
            h.className = 'arr-tooltip-title';
            h.textContent = title;
            tip.appendChild(h);
        }
        var p = document.createElement('span');
        p.textContent = text;
        tip.appendChild(p);

        tip.removeAttribute('aria-hidden');
        tip.classList.add('arr-tooltip--visible');
        current = trigger;
        position(trigger);
    }

    function hide() {
        if (!tip) return;
        tip.classList.remove('arr-tooltip--visible');
        tip.setAttribute('aria-hidden', 'true');
        current = null;
    }

    function position(trigger) {
        if (!tip) return;
        var rect = trigger.getBoundingClientRect();
        var tipRect = tip.getBoundingClientRect();
        var scrollY = window.scrollY || document.documentElement.scrollTop;
        var scrollX = window.scrollX || document.documentElement.scrollLeft;
        var viewW = window.innerWidth;

        // Default: above the trigger, centred
        var top = rect.top + scrollY - tipRect.height - 8;
        var left = rect.left + scrollX + (rect.width / 2) - (tipRect.width / 2);

        // Flip below if not enough room above
        if (top < scrollY + 4) {
            top = rect.bottom + scrollY + 8;
        }

        // Clamp horizontally within viewport
        if (left < 8) left = 8;
        if (left + tipRect.width > viewW - 8) left = viewW - tipRect.width - 8;

        tip.style.top = top + 'px';
        tip.style.left = left + 'px';
    }

    function attachTooltips(root) {
        var elements = (root || document).querySelectorAll('[data-tooltip]');
        elements.forEach(function (el) {
            if (el._tooltipBound) return;
            el._tooltipBound = true;

            el.addEventListener('mouseenter', function () { show(el); });
            el.addEventListener('mouseleave', hide);
            el.addEventListener('focus', function () { show(el); });
            el.addEventListener('blur', hide);

            // Mobile tap toggle
            el.addEventListener('touchstart', function (e) {
                if (current === el) {
                    hide();
                } else {
                    e.preventDefault();
                    show(el);
                }
            }, { passive: false });
        });
    }

    // Initial attach
    document.addEventListener('DOMContentLoaded', function () {
        attachTooltips(document);

        // Hide on outside tap
        document.addEventListener('touchstart', function (e) {
            if (current && !current.contains(e.target)) hide();
        });

        // Re-attach when HTMX swaps in new content
        document.body.addEventListener('htmx:afterSwap', function (e) {
            attachTooltips(e.detail.target);
        });
    });

    // Expose for manual use (e.g. Alpine.js dynamic content)
    window.softarrTooltips = { attach: attachTooltips, hide: hide };
})();
