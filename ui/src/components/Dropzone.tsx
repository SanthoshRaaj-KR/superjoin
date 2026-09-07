import { useRef, useState } from "react";

import { useStore } from "../store";

export default function Dropzone() {
  const { upload } = useStore();
  const [hot, setHot] = useState(false);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const take = async (files: FileList | File[]) => {
    setBusy(true);
    try {
      await upload(files);
    } finally {
      setBusy(false);
    }
  };

  return (
    <button
      type="button"
      className={`dropzone ${hot ? "hot" : ""}`}
      onClick={() => inputRef.current?.click()}
      onDragOver={(e) => {
        e.preventDefault();
        setHot(true);
      }}
      onDragLeave={() => setHot(false)}
      onDrop={(e) => {
        e.preventDefault();
        setHot(false);
        if (e.dataTransfer.files.length) void take(e.dataTransfer.files);
      }}
    >
      <input
        ref={inputRef}
        type="file"
        accept="application/pdf"
        multiple
        hidden
        onChange={(e) => {
          if (e.target.files?.length) void take(e.target.files);
          e.target.value = "";
        }}
      />
      {busy
        ? "UPLOADING…"
        : "DROP PDFs TO INGEST, OR CLICK TO BROWSE · extraction, grounding and axis resolution run per page"}
    </button>
  );
}
