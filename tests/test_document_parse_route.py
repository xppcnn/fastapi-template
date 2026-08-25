from app.main import app


def _all_paths() -> list[str]:
    paths: list[str] = []

    def walk(routes) -> None:
        for route in routes:
            if type(route).__name__ == "_IncludedRouter":
                walk(route.original_router.routes)
            elif hasattr(route, "path"):
                paths.append(route.path)

    walk(app.routes)
    return paths


def test_parse_route_path_is_correct() -> None:
    paths = _all_paths()
    assert (
        "/projects/{project_id}/documents/{document_id}/versions/{version_id}/parse"
        in paths
    )
    assert not any("projects{project_id}" in p for p in paths)
