# v13 - Django SECRET_KEY startup fix

- Prevent generated `DJANGO_SECRET_KEY` values from beginning with `$`.
- Rotate an already-existing leading-`$` Django key during the next env rewrite while preserving the PostgreSQL password.
- Reject leading-`$` Django keys in both secret validation paths so the Security Gate/runtime validation can catch them before startup.
- Regression tests cover both secret generators and legacy-key rotation.

Reason: `django-environ` treats environment values beginning with `$` as references to another environment variable. A random secret beginning with `$` therefore caused the Django container to restart and the instance start to fail.
