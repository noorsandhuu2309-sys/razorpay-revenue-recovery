import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

afterEach(() => { cleanup() })

// jsdom implements neither of these, and the workspace shell calls both on
// mount. Without the stubs every component test fails on an unrelated crash.
globalThis.ResizeObserver ??= class {
  observe() {} unobserve() {} disconnect() {}
} as unknown as typeof ResizeObserver

// Same gap, same reason: `useVirtualRows` calls `scrollTo` on the scroll
// container when it mounts, and jsdom defines the property but not the method.
if (!Element.prototype.scrollTo) {
  Element.prototype.scrollTo = function scrollTo() {} as typeof Element.prototype.scrollTo
}

if (!window.matchMedia) {
  window.matchMedia = ((q: string) => ({
    matches: false, media: q, onchange: null,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
}

// Nothing in the suite may reach the network. A test that needs a response
// stubs it explicitly; anything else failing loudly here is the correct
// outcome, because a silent fetch means the test was measuring the server.
globalThis.fetch = vi.fn(() => Promise.reject(
  new Error('unstubbed fetch — mock the api module instead'),
)) as unknown as typeof fetch
