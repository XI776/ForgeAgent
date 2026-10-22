const fallbackRpcUrl = 'ws://127.0.0.1:8765/rpc'

export type RpcUrlOptions = {
  configured?: string
  dev?: boolean
  protocol?: string
  host?: string
  pathname?: string
}

export function resolveRpcUrl(options: RpcUrlOptions = {}): string {
  if (options.configured) return options.configured
  // Vite dev serves the UI from :5173 while the Host listens on :8765.
  if (options.dev) return fallbackRpcUrl
  const protocol = options.protocol || ''
  // A production build served by the Host itself uses the same origin, which
  // keeps WebSocket and artifact URLs working behind Docker port mapping.
  if ((protocol === 'http:' || protocol === 'https:') && options.host) {
    const pathname = options.pathname || '/'
    const basePath = pathname.endsWith('/') ? pathname : pathname.slice(0, pathname.lastIndexOf('/') + 1) || '/'
    return `${protocol === 'https:' ? 'wss:' : 'ws:'}//${options.host}${basePath}rpc`
  }
  // Packaged Electron loads index.html from file:// and needs the loopback Host.
  return fallbackRpcUrl
}

export function rpcUrl(): string {
  return resolveRpcUrl({
    configured: import.meta.env.VITE_RPC_URL,
    dev: import.meta.env.DEV,
    protocol: typeof window === 'undefined' ? '' : window.location.protocol,
    host: typeof window === 'undefined' ? '' : window.location.host,
    pathname: typeof window === 'undefined' ? '/' : window.location.pathname,
  })
}

export function httpBaseUrl(value = rpcUrl()): string {
  const url = new URL(value)
  url.protocol = url.protocol === 'wss:' ? 'https:' : 'http:'
  url.pathname = url.pathname.replace(/\/rpc\/?$/, '') || '/'
  url.search = ''
  url.hash = ''
  return url.toString().replace(/\/$/, '')
}

export function artifactContentUrl(id: string): string {
  return `${httpBaseUrl()}/artifacts/${encodeURIComponent(id)}/content`
}
