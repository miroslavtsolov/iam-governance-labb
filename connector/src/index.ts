import {
  Context,
  createConnector,
  readConfig,
  Response,
  logger,
  ConnectorError,
  StdAccountCreateInput, StdAccountCreateOutput,
  StdAccountDeleteInput, StdAccountDeleteOutput,
  StdAccountDisableInput, StdAccountDisableOutput,
  StdAccountEnableInput, StdAccountEnableOutput,
  StdAccountListInput, StdAccountListOutput,
  StdAccountReadInput, StdAccountReadOutput,
  StdAccountUpdateInput, StdAccountUpdateOutput,
  StdEntitlementListInput, StdEntitlementListOutput,
  StdTestConnectionInput, StdTestConnectionOutput,
} from '@sailpoint/connector-sdk'

type FinUser = {
  username: string
  email: string
  display_name: string
  employee_id: string | null
  department: string | null
  disabled: boolean
  entitlements: string[]
}

const baseUrl = (): string => {
  const cfg = readConfig() as { baseUrl?: string }
  return (cfg?.baseUrl || 'http://localhost:8001').replace(/\/$/, '')
}

async function api<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${baseUrl()}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) throw new ConnectorError(`FinApp ${method} ${path} -> HTTP ${r.status}`)
  return r.status === 204 ? (undefined as T) : ((await r.json()) as T)
}

const toAccount = (u: FinUser) => ({
  identity: u.username,
  uuid: u.username,
  disabled: u.disabled,
  attributes: {
    username: u.username,
    email: u.email,
    display_name: u.display_name,
    employee_id: u.employee_id ?? '',
    entitlements: u.entitlements,
  },
})

export const connector = async () =>
  createConnector()
    .stdTestConnection(async (_c: Context, _i: StdTestConnectionInput, res: Response<StdTestConnectionOutput>) => {
      await api('GET', '/health')
      logger.info('FinApp reachable')
      res.send({})
    })
    // "Aggregation": return every account in the source
    .stdAccountList(async (_c: Context, _i: StdAccountListInput, res: Response<StdAccountListOutput>) => {
      const users = await api<FinUser[]>('GET', '/users')
      logger.info(`aggregating ${users.length} accounts`)
      for (const u of users) res.send(toAccount(u))
    })
    .stdAccountRead(async (_c: Context, i: StdAccountReadInput, res: Response<StdAccountReadOutput>) => {
      res.send(toAccount(await api<FinUser>('GET', `/users/${encodeURIComponent(i.identity)}`)))
    })
    // Entitlement catalogue (what ISC shows as entitlements on a source)
    .stdEntitlementList(async (_c: Context, i: StdEntitlementListInput, res: Response<StdEntitlementListOutput>) => {
      const ents = await api<{ id: string; description: string; privileged: boolean }[]>('GET', '/entitlements')
      for (const e of ents) res.send({ identity: e.id, uuid: e.id, type: i.type || 'entitlement', attributes: { ...e } })
    })
    // Provisioning
    .stdAccountCreate(async (_c: Context, i: StdAccountCreateInput, res: Response<StdAccountCreateOutput>) => {
      const a = i.attributes ?? {}
      const u = await api<FinUser>('POST', '/users', {
        username: i.identity ?? a.username,
        email: a.email,
        display_name: a.display_name,
        employee_id: a.employee_id,
        department: a.department,
        entitlements: [].concat(a.entitlements ?? []),
      })
      res.send(toAccount(u))
    })
    .stdAccountUpdate(async (_c: Context, i: StdAccountUpdateInput, res: Response<StdAccountUpdateOutput>) => {
      const add: string[] = []
      const remove: string[] = []
      for (const ch of i.changes) {
        if (ch.attribute !== 'entitlements') continue
        const vals: string[] = [].concat(ch.value as never)
        if (ch.op === 'Add') add.push(...vals)
        if (ch.op === 'Remove') remove.push(...vals)
      }
      const u = await api<FinUser>('PATCH', `/users/${encodeURIComponent(i.identity)}`, { add, remove })
      res.send(toAccount(u))
    })
    .stdAccountDisable(async (_c: Context, i: StdAccountDisableInput, res: Response<StdAccountDisableOutput>) => {
      res.send(toAccount(await api<FinUser>('PATCH', `/users/${encodeURIComponent(i.identity)}`, { disabled: true })))
    })
    .stdAccountEnable(async (_c: Context, i: StdAccountEnableInput, res: Response<StdAccountEnableOutput>) => {
      res.send(toAccount(await api<FinUser>('PATCH', `/users/${encodeURIComponent(i.identity)}`, { disabled: false })))
    })
    .stdAccountDelete(async (_c: Context, i: StdAccountDeleteInput, res: Response<StdAccountDeleteOutput>) => {
      await api('DELETE', `/users/${encodeURIComponent(i.identity)}`)
      res.send({})
    })
