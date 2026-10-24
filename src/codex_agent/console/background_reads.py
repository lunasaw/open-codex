"""Read-only UI queries retained in diagnostics, outside conversation history."""

BACKGROUND_READS = frozenset({
    'mcpServerStatus/list', 'config/read', 'configRequirements/read', 'model/list',
    'thread/list', 'thread/read', 'thread/loaded/list', 'thread/queue/list', 'thread/goal/get',
    'thread/backgroundTerminals/list', 'threadSection/list', 'project/list', 'skills/list',
    'app/list', 'app/installed', 'app/read', 'plugin/list', 'plugin/read', 'plugin/installed',
    'account/read', 'account/rateLimits/read', 'account/gatewayOAuth/read', 'server/diagnostics',
    'permissionProfile/list', 'collaborationMode/list', 'experimentalFeature/list',
})


def background_step(row: dict) -> bool:
    return (row.get('type') == 'request' and not row.get('interactive')
            and row.get('method') in BACKGROUND_READS)
