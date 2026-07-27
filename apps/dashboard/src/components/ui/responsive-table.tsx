import type { ReactNode } from "react";
import { Card } from "@/components/ui/card";

export interface Column<T> {
  key: string;
  header: string;
  align?: "left" | "right";
  render: (row: T) => ReactNode;
}

interface ResponsiveTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  /** Card rendering for narrow screens. */
  renderCard: (row: T) => ReactNode;
}

/** Table on md+, stacked cards below — no horizontal page scroll. */
export function ResponsiveTable<T>({
  columns,
  rows,
  rowKey,
  renderCard,
}: ResponsiveTableProps<T>) {
  return (
    <>
      <Card className="hidden overflow-x-auto md:block">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted-foreground">
              {columns.map((column) => (
                <th
                  key={column.key}
                  className={
                    column.align === "right"
                      ? "px-4 py-2.5 text-right font-medium"
                      : "px-4 py-2.5 font-medium"
                  }
                >
                  {column.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={rowKey(row)}
                className="border-b border-border last:border-0 hover:bg-muted/50"
              >
                {columns.map((column) => (
                  <td
                    key={column.key}
                    className={
                      column.align === "right"
                        ? "px-4 py-2.5 text-right tabular-nums"
                        : "px-4 py-2.5"
                    }
                  >
                    {column.render(row)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <div className="space-y-2 md:hidden">
        {rows.map((row) => (
          <Card key={rowKey(row)} className="p-4">
            {renderCard(row)}
          </Card>
        ))}
      </div>
    </>
  );
}
