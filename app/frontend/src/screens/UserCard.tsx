import { useMe } from '../api/hooks'

/** Compact user profile chip for the header (U97): a gradient avatar initial + name/email of the
 * signed-in end user, from the Databricks Apps identity headers (D48/U91). Renders nothing when no
 * identity is forwarded (local/anon dev). */
export function UserCard() {
  const { data } = useMe()
  const name = data?.username || data?.email
  if (!name) return null
  const initial = name.trim()[0]?.toUpperCase() ?? 'U'
  const showEmail = data?.email && data.email !== name
  return (
    <div className="flex items-center gap-2 rounded-full border border-slate-200 bg-white py-1 pl-1 pr-3 shadow-sm">
      <span
        className="flex h-7 w-7 items-center justify-center rounded-full bg-gradient-to-br from-indigo-500 to-sky-500 text-xs font-semibold text-white"
        aria-hidden
      >
        {initial}
      </span>
      <div className="leading-tight">
        <p className="max-w-[12rem] truncate text-xs font-medium text-slate-700">{name}</p>
        {showEmail && <p className="max-w-[12rem] truncate text-[10px] text-slate-400">{data?.email}</p>}
      </div>
    </div>
  )
}
