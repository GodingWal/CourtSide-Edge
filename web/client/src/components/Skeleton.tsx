import React from 'react';

/* ------------------------------------------------------------------ */
/*  Base Skeleton                                                      */
/* ------------------------------------------------------------------ */

interface SkeletonProps {
  className?: string;
}

export function Skeleton({ className = '' }: SkeletonProps) {
  return (
    <div
      className={`animate-pulse rounded-lg bg-cs-dark/80 ${className}`}
    />
  );
}

/* ------------------------------------------------------------------ */
/*  Skeleton Card                                                      */
/* ------------------------------------------------------------------ */

export function SkeletonCard({ className = '' }: SkeletonProps) {
  return (
    <div className={`cs-card space-y-4 p-5 ${className}`}>
      <Skeleton className="h-4 w-[60%]" />
      <Skeleton className="h-4 w-[80%]" />
      <Skeleton className="h-4 w-[40%]" />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Skeleton Table                                                     */
/* ------------------------------------------------------------------ */

export function SkeletonTable({ className = '' }: SkeletonProps) {
  return (
    <div className={`cs-card overflow-hidden p-0 ${className}`}>
      {/* Header row */}
      <div className="flex items-center gap-4 border-b border-cs-border/40 bg-cs-dark/30 px-5 py-3">
        <Skeleton className="h-3 w-24" />
        <Skeleton className="h-3 w-32" />
        <Skeleton className="h-3 w-20 ml-auto" />
        <Skeleton className="h-3 w-16" />
      </div>

      {/* Body rows */}
      {Array.from({ length: 5 }).map((_, i) => (
        <div
          key={i}
          className="flex items-center gap-4 border-b border-cs-border/20 px-5 py-3 last:border-b-0"
        >
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-3 w-32" />
          <Skeleton className="h-3 w-20 ml-auto" />
          <Skeleton className="h-3 w-16" />
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Skeleton Chart                                                     */
/* ------------------------------------------------------------------ */

export function SkeletonChart({ className = '' }: SkeletonProps) {
  return (
    <div className={`cs-card p-5 ${className}`}>
      {/* Chart title placeholder */}
      <Skeleton className="mb-4 h-4 w-36" />
      {/* Chart area */}
      <Skeleton className="h-64 w-full rounded-lg" />
    </div>
  );
}
