import { createContext } from 'react'
import type { HoveredKey } from './edgeDisplay'

// Lets a TableNode report which PK/FK column the pointer is over, without threading
// callbacks through React Flow node `data` (which would churn every node's data object on
// each hover). The setters here are stable (useCallback in App), so providing them via
// context re-renders nothing on its own -- only the resulting highlight/label state does.
export interface ErdInteraction {
  onKeyEnter: (key: HoveredKey) => void
  onKeyLeave: () => void
  // Toggle a table's expanded state (show all columns vs the capped view). Changing it
  // re-runs layout, since the card's height changes.
  onToggleExpand: (nodeId: string) => void
  // Collapse/expand a schema group box (grouped mode): hides/shows its tables.
  onToggleGroup: (groupId: string) => void
}

export const ErdInteractionContext = createContext<ErdInteraction>({
  onKeyEnter: () => {},
  onKeyLeave: () => {},
  onToggleExpand: () => {},
  onToggleGroup: () => {},
})
