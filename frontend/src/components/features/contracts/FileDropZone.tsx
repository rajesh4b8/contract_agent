import React, { useCallback, useId, useState } from 'react';
import { Loader } from '../../shared/ui/loader';

interface FileDropZoneProps {
  onFile: (file: File) => void;
  busy?: boolean;
  busyLabel?: string;
  hint?: string;
}

const MAX_BYTES = 50 * 1024 * 1024;

/** Drop a PDF, or click to browse. Used by both upload paths. */
export const FileDropZone: React.FC<FileDropZoneProps> = ({
  onFile,
  busy = false,
  busyLabel = 'Processing PDF…',
  hint = 'Maximum file size: 50MB',
}) => {
  const inputId = useId();
  const [dragActive, setDragActive] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const accept = useCallback(
    (files: FileList | null) => {
      const file = files?.[0];
      if (!file) return;
      if (!file.name.toLowerCase().endsWith('.pdf')) {
        setProblem('Only PDF files are supported.');
        return;
      }
      if (file.size > MAX_BYTES) {
        setProblem('That file is over the 50MB limit.');
        return;
      }
      setProblem(null);
      onFile(file);
    },
    [onFile],
  );

  const handleDrag = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(e.type === 'dragenter' || e.type === 'dragover');
  }, []);

  return (
    <div className="space-y-2">
      <div
        className={`border-2 border-dashed rounded-lg p-8 text-center transition-colors ${
          dragActive ? 'border-blue-500 bg-blue-50' : 'border-slate-300 hover:border-slate-400'
        } ${busy ? 'pointer-events-none opacity-60' : 'cursor-pointer'}`}
        onDragEnter={handleDrag}
        onDragLeave={handleDrag}
        onDragOver={handleDrag}
        onDrop={(e) => {
          handleDrag(e);
          setDragActive(false);
          accept(e.dataTransfer.files);
        }}
        onClick={() => document.getElementById(inputId)?.click()}
      >
        {busy ? (
          <div className="space-y-2">
            <Loader className="mx-auto" />
            <p className="text-sm text-slate-600">{busyLabel}</p>
          </div>
        ) : (
          <div className="space-y-2">
            <div className="text-4xl">📄</div>
            <p className="text-sm font-medium text-slate-700">Drop a PDF here or click to browse</p>
            <p className="text-xs text-slate-500">{hint}</p>
          </div>
        )}
      </div>

      <input
        id={inputId}
        type="file"
        accept=".pdf"
        className="hidden"
        disabled={busy}
        onChange={(e) => {
          accept(e.target.files);
          // So re-selecting the same file fires a change event again.
          e.target.value = '';
        }}
      />

      {problem && <p className="text-sm text-red-600">{problem}</p>}
    </div>
  );
};
