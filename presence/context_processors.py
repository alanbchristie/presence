from django.conf import settings


def version(request):
    """Expose the running application version to every template.

    Used by the About modal in ``base.html``, which appears on every page
    (including the anonymous login page), so the value has to be available in
    all template contexts rather than passed per-view.
    """
    return {"version": settings.VERSION}
