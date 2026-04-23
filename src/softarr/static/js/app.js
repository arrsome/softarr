// Softarr client-side utilities
// HTMX and Alpine.js are loaded via CDN in base.html.
// To vendor them locally, download the minified builds and replace
// the CDN script tags with references to this directory.
//
// HTMX: https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js
// Alpine: https://unpkg.com/alpinejs@3.14.3/dist/cdn.min.js

document.addEventListener('DOMContentLoaded', function() {
    // Highlight active nav link using ARR-style left-border indicator
    const path = window.location.pathname;
    document.querySelectorAll('nav a.arr-nav-link').forEach(function(link) {
        const href = link.getAttribute('href');
        if (href === path) {
            link.classList.add('arr-active');
            link.style.borderLeftColor = '#1e90ff';
        }
    });
});

// Submit the "Add Software" form as JSON so the API receives the
// correct content type and list fields are properly serialised.
async function submitSoftwareForm(form) {
    var csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';

    var aliasesRaw = form.querySelector('[name="aliases"]').value;
    var aliases = aliasesRaw
        ? aliasesRaw.split(',').map(function(a) { return a.trim(); }).filter(Boolean)
        : [];

    var checkedOS = form.querySelectorAll('[name="supported_os"]:checked');
    var supportedOs = Array.from(checkedOS).map(function(cb) { return cb.value; });

    var body = {
        canonical_name: form.querySelector('[name="canonical_name"]').value,
        expected_publisher: form.querySelector('[name="expected_publisher"]').value || null,
        aliases: aliases,
        supported_os: supportedOs,
        architecture: form.querySelector('[name="architecture"]').value || null,
        notes: form.querySelector('[name="notes"]').value || null,
    };

    try {
        var resp = await fetch('/api/v1/software/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': csrfToken,
            },
            body: JSON.stringify(body),
        });
        if (resp.ok) {
            form.reset();
            location.reload();
        } else {
            var err = await resp.json();
            var msg = typeof err.detail === 'string'
                ? err.detail
                : JSON.stringify(err.detail);
            alert('Error: ' + msg);
        }
    } catch (e) {
        alert('Error: ' + e.message);
    }
}
