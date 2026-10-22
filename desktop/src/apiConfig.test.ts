import { describe, expect, it } from 'vitest'
import { httpBaseUrl, resolveRpcUrl } from './apiConfig'

describe('HTTP endpoint configuration', () => {
  it('derives HTTP and HTTPS origins from RPC URLs', () => {
    expect(httpBaseUrl('ws://127.0.0.1:8765/rpc')).toBe('http://127.0.0.1:8765')
    expect(httpBaseUrl('wss://agent.example.test/base/rpc')).toBe('https://agent.example.test/base')
  })
})

describe('RPC endpoint resolution', () => {
  it('uses the explicit VITE_RPC_URL when provided', () => {
    expect(resolveRpcUrl({ configured: 'wss://agent.example.test/rpc', dev: true })).toBe('wss://agent.example.test/rpc')
  })

  it('keeps the loopback Host during Vite development', () => {
    expect(resolveRpcUrl({ dev: true, protocol: 'http:', host: '127.0.0.1:5173' })).toBe('ws://127.0.0.1:8765/rpc')
  })

  it('uses the same origin for a production web build', () => {
    expect(resolveRpcUrl({ protocol: 'http:', host: '192.168.1.5:8765' })).toBe('ws://192.168.1.5:8765/rpc')
    expect(resolveRpcUrl({ protocol: 'https:', host: 'agent.example.test' })).toBe('wss://agent.example.test/rpc')
  })

  it('keeps a reverse-proxy subpath', () => {
    expect(resolveRpcUrl({ protocol: 'https:', host: 'agent.example.test', pathname: '/forgeagent/' }))
      .toBe('wss://agent.example.test/forgeagent/rpc')
    expect(resolveRpcUrl({ protocol: 'http:', host: 'agent.example.test:8765', pathname: '/app/index.html' }))
      .toBe('ws://agent.example.test:8765/app/rpc')
  })

  it('falls back to the loopback Host for packaged Electron', () => {
    expect(resolveRpcUrl({ protocol: 'file:', host: '' })).toBe('ws://127.0.0.1:8765/rpc')
  })
})
