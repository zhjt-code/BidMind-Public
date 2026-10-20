import assert from 'node:assert/strict'
import test from 'node:test'

import { parseFeatureFlag } from '../src/config/features.ts'

test('parseFeatureFlag recognizes explicit enabled values', () => {
  for (const value of ['true', 'TRUE', '1', 'yes', 'on']) {
    assert.equal(parseFeatureFlag(value), true)
  }
})

test('parseFeatureFlag defaults missing or disabled values to false', () => {
  for (const value of [undefined, '', 'false', '0', 'no', 'off']) {
    assert.equal(parseFeatureFlag(value), false)
  }
})
