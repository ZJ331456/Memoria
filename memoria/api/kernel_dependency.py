"""Only the non-serving OpenAPI builder may omit a tenant Kernel."""

from fastapi import Depends, HTTPException


def require_kernel(kernel):
    def check():
        if kernel is None:
            raise HTTPException(503, "A tenant-bound memory kernel is required")

    return Depends(check)
