import { ChevronLeftIcon, ChevronRightIcon } from 'lucide-react'

/** Compact prev / page-of / next control. Renders nothing for a single page. */
export default function Paginator({
  page, pageSize, total, onPage
}: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  if (totalPages <= 1) return null
  const from = total === 0 ? 0 : page * pageSize + 1
  const to = Math.min(total, (page + 1) * pageSize)
  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 10,
      padding: '11px 15px', borderTop: '1px solid var(--line)'
    }}>
      <span className="num" style={{ fontSize: 12, color: 'var(--muted)', marginRight: 'auto' }}>
        {from}–{to} of {total.toLocaleString()}
      </span>
      <button className="btn sm ghost" disabled={page <= 0} onClick={() => onPage(page - 1)}>
        <ChevronLeftIcon className="" />Prev
      </button>
      <span className="num" style={{ fontSize: 12, color: 'var(--text2)', fontWeight: 600 }}>
        {page + 1} / {totalPages}
      </span>
      <button className="btn sm ghost" disabled={page >= totalPages - 1} onClick={() => onPage(page + 1)}>
        Next<ChevronRightIcon className="" />
      </button>
    </div>
  )
}
