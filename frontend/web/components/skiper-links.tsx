"use client";

/**
 * Client boundary for the Skiper UI animated link components.
 *
 * skiper40.tsx imports `next/link` directly while the file itself is a
 * server component; rendering its exports from a server component is fine,
 * but exporting this tiny wrapper keeps usage simple and lets any client
 * component consume the animated links without registry edits. The registry
 * file stays unmodified.
 */

import { Link001, Link002, Link003, Link004 } from "@/components/ui/skiper-ui/skiper40";

export { Link001, Link002, Link003, Link004 };
