import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'

export function EmptyState({
  title,
  description,
  linkTo,
  linkLabel,
  icon,
}: {
  title: string
  description: string
  linkTo?: string
  linkLabel?: string
  icon?: ReactNode
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 px-6 text-center">
      {icon && <div className="text-slate-300">{icon}</div>}
      <h2 className="text-xl font-semibold text-slate-800">{title}</h2>
      <p className="max-w-md text-sm text-slate-500">{description}</p>
      {linkTo && linkLabel && (
        <Link
          to={linkTo}
          className="mt-2 inline-flex min-h-11 items-center rounded-lg bg-teal-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-teal-700"
        >
          {linkLabel}
        </Link>
      )}
    </div>
  )
}
