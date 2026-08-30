import { useCallback, useState } from 'react'
import { EditionsList } from '../components/EditionsList'
import { ProgressPanel } from '../components/ProgressPanel'
import { useCreateEdition, useJobStatus, useParseMetadata } from '../lib/queries'

export function Dashboard() {
  const [file, setFile] = useState<File | null>(null)
  const [edition, setEdition] = useState('')
  const [date, setDate] = useState('')
  const [isDragging, setIsDragging] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)

  const parseMetadata = useParseMetadata()
  const createEdition = useCreateEdition()
  const jobQuery = useJobStatus(jobId)

  const handleFile = useCallback(
    (selected: File) => {
      setFile(selected)
      setJobId(null)
      parseMetadata.mutate(selected, {
        onSuccess: (result) => {
          setEdition(result.edition ?? '')
          setDate(result.date ?? '')
        },
      })
    },
    [parseMetadata],
  )

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    setIsDragging(false)
    const dropped = e.dataTransfer.files[0]
    if (dropped) handleFile(dropped)
  }

  const handleExtract = () => {
    if (!file || !edition || !date) return
    createEdition.mutate(
      { file, edition, date },
      { onSuccess: (result) => setJobId(result.job_id) },
    )
  }

  const canExtract = Boolean(file && edition && date) && !createEdition.isPending && jobId === null

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-8 px-8 py-10">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900">Ingest a new edition</h1>
        <p className="mt-1 text-sm text-slate-500">Drop a PDF e-paper to extract its articles.</p>
      </div>

      <div
        onDragOver={(e) => {
          e.preventDefault()
          setIsDragging(true)
        }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={handleDrop}
        className={`flex flex-col items-center justify-center gap-3 rounded-2xl border-2 border-dashed px-8 py-12 text-center transition-colors ${
          isDragging ? 'border-teal-400 bg-teal-50' : 'border-slate-300 bg-white'
        }`}
      >
        <input
          id="pdf-input"
          type="file"
          accept="application/pdf"
          className="hidden"
          onChange={(e) => {
            const selected = e.target.files?.[0]
            if (selected) handleFile(selected)
          }}
        />
        <p className="text-sm text-slate-500">Drag & drop a PDF here, or</p>
        <label
          htmlFor="pdf-input"
          className="cursor-pointer rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          Browse files
        </label>

        {file && (
          <div className="mt-4 flex items-center gap-2 rounded-full bg-slate-100 px-3 py-1.5 text-sm text-slate-700">
            <span>{file.name}</span>
            {parseMetadata.isPending && <span className="text-xs text-slate-400">reading metadata...</span>}
          </div>
        )}
      </div>

      {file && (
        <div className="flex flex-col gap-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex gap-4">
            <label className="flex flex-1 flex-col gap-1 text-sm">
              <span className="font-medium text-slate-600">Edition</span>
              <input
                value={edition}
                onChange={(e) => setEdition(e.target.value)}
                placeholder="e.g. delhi"
                disabled={jobId !== null}
                className="rounded-lg border border-slate-300 px-3 py-2 text-sm disabled:bg-slate-50"
              />
            </label>
            <label className="flex flex-1 flex-col gap-1 text-sm">
              <span className="font-medium text-slate-600">Date</span>
              <input
                type="date"
                value={date}
                onChange={(e) => setDate(e.target.value)}
                disabled={jobId !== null}
                className="rounded-lg border border-slate-300 px-3 py-2 text-sm disabled:bg-slate-50"
              />
            </label>
          </div>
          {!parseMetadata.isPending && parseMetadata.isSuccess && !parseMetadata.data.edition && (
            <p className="text-xs text-amber-600">
              Couldn't read the edition/date from this PDF's masthead - please fill them in.
            </p>
          )}

          {jobId === null ? (
            <button
              onClick={handleExtract}
              disabled={!canExtract}
              className="rounded-lg bg-teal-600 px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-teal-700 disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              {createEdition.isPending ? 'Starting...' : 'Extract'}
            </button>
          ) : jobQuery.data ? (
            <ProgressPanel job={jobQuery.data} />
          ) : (
            <p className="text-sm text-slate-400">Starting job...</p>
          )}
        </div>
      )}

      <div>
        <h2 className="mb-3 text-lg font-semibold text-slate-900">Previously ingested editions</h2>
        <EditionsList />
      </div>
    </div>
  )
}
