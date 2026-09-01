"""
Shared ContextVars for the MCP server layer.

Lives in its own module to avoid circular imports between
mcp_server_manager.py and server.py.
"""

from contextvars import ContextVar
from typing import Optional

# Set server-side in proxy_server.py route handlers when a request arrives via
# /toolset/{name}/mcp or the toolset fallback in dynamic_mcp_route.
# Never populated from client-supplied headers.
_mcp_active_toolset_id: ContextVar[Optional[str]] = ContextVar("_mcp_active_toolset_id", default=None)

# Per-request merged InitializeResult.instructions; set in MCP HTTP/SSE handlers.
_mcp_gateway_initialize_instructions: ContextVar[Optional[str]] = ContextVar(
    "_mcp_gateway_initialize_instructions", default=None
)

# Per-request scoped server name; set in MCP HTTP/SSE handlers when the path
# identifies exactly one upstream server. Never populated from client-supplied headers.
_mcp_gateway_server_name: ContextVar[Optional[str]] = ContextVar("_mcp_gateway_server_name", default=None)

# Per-request scoped server version (upstream InitializeResult.serverInfo.version);
# set alongside _mcp_gateway_server_name so a single-server endpoint reports the
# upstream's version instead of the gateway constant. Public name on purpose: it is
# read from server.py and the underscore siblings above already trip reportPrivateUsage.
mcp_gateway_server_version: ContextVar[Optional[str]] = ContextVar("mcp_gateway_server_version", default=None)
