import assert from 'node:assert/strict'
import test from 'node:test'

import {
  ApiRequestError,
  extractApiErrorMessage,
  extractStructuredApiError,
  getReadableErrorMessage,
  shouldShowMaintenanceMessage,
  SYSTEM_MAINTENANCE_MESSAGE,
} from '../src/services/apiError.ts'

test('502 携带业务错误时不应误判为系统维护', () => {
  assert.equal(
    shouldShowMaintenanceMessage(502, {
      detail: '支付宝下单失败: isv.invalid-signature',
    }),
    false,
  )
})

test('502 仅返回网关占位文本时应提示系统维护', () => {
  assert.equal(shouldShowMaintenanceMessage(502, 'Bad Gateway'), true)
  assert.equal(shouldShowMaintenanceMessage(503, '<html>503 Service Unavailable</html>'), true)
})

test('应优先提取后端可读错误详情', () => {
  assert.equal(
    extractApiErrorMessage({
      detail: [
        { msg: '签名校验失败' },
        { msg: '请检查支付宝平台公钥配置' },
      ],
    }),
    '签名校验失败; 请检查支付宝平台公钥配置',
  )

  assert.equal(
    extractApiErrorMessage({ detail: SYSTEM_MAINTENANCE_MESSAGE }),
    SYSTEM_MAINTENANCE_MESSAGE,
  )
})

test('Axios 错误应优先使用后端 detail 而不是通用状态文本', () => {
  const error = {
    isAxiosError: true,
    message: 'Request failed with status code 404',
    response: {
      status: 404,
      data: { detail: '绑定不存在' },
    },
  }

  assert.equal(getReadableErrorMessage(error), '绑定不存在')
})

test('Axios 错误无后端 detail 时应回退到状态文本', () => {
  const error = {
    isAxiosError: true,
    message: 'Request failed with status code 404',
    response: {
      status: 404,
      data: {},
    },
  }

  assert.equal(getReadableErrorMessage(error), 'Request failed with status code 404')
})

test('ApiRequestError 应保留 HTTP 状态码', () => {
  const error = new ApiRequestError('绑定不存在', 404)

  assert.equal(error.message, '绑定不存在')
  assert.equal(error.statusCode, 404)
})

test('ApiRequestError 可携带结构化错误码与附加字段 ', () => {
  const error = new ApiRequestError('余额不足，可用0, 需要400', 403, {
    errorCode: 'INSUFFICIENT_BALANCE',
    details: { available: 0, required: 400 },
  })

  assert.equal(error.errorCode, 'INSUFFICIENT_BALANCE')
  assert.deepEqual(error.details, { available: 0, required: 400 })
})

test('extractStructuredApiError 识别 {detail: {error_code}} 嵌套形态', () => {
  const payload = {
    code: 403,
    message: '请求处理失败',
    detail: {
      error_code: 'INSUFFICIENT_BALANCE',
      message: '余额不足，可用0, 需要400',
      available: 0,
      required: 400,
    },
  }

  const structured = extractStructuredApiError(payload)
  assert.ok(structured)
  assert.equal(structured.errorCode, 'INSUFFICIENT_BALANCE')
  assert.equal(structured.message, '余额不足，可用0, 需要400')
  assert.deepEqual(structured.details, { available: 0, required: 400 })
})

test('extractStructuredApiError 对普通字符串 detail 返回 null', () => {
  assert.equal(extractStructuredApiError({ detail: '权限不足' }), null)
  assert.equal(extractStructuredApiError({ detail: [{ msg: '校验失败' }] }), null)
  assert.equal(extractStructuredApiError('Bad Gateway'), null)
  assert.equal(extractStructuredApiError(null), null)
})

test('extractApiErrorMessage 兼容结构化 dict detail 的 message 提取', () => {
  assert.equal(
    extractApiErrorMessage({
      detail: { error_code: 'INSUFFICIENT_BALANCE', message: '余额不足，可用0, 需要400' },
    }),
    '余额不足，可用0, 需要400',
  )
})
