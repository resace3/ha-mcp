# Home Assistant MCP Server - DAG Studio

Build and review causal DAG hypotheses locally through a tightly scoped Model
Context Protocol profile.

## About

This fork exposes exactly ten DAG Studio tools. It does not register generic
Home Assistant device, service, automation, or configuration write tools. Raw
Home Assistant history is processed locally and is never returned as time-series
rows to an MCP client.

**Key Features:**
- Authenticated, administrator-only Home Assistant ingress
- Local, bounded history processing with sensitive domains denied by default
- Revision-safe DAG storage, validation, snapshots, and deletion backups
- Single-use confirmation tokens for approval and deletion
- Server-side tool allowlisting and security policy enforcement

## Installation

See the [Documentation](DOCS.md) tab for complete installation and configuration instructions.

## Support

- **Documentation**: [DOCS.md](DOCS.md)
- **Issues**: https://github.com/resace3/ha-mcp/issues
- **Repository**: https://github.com/resace3/ha-mcp
