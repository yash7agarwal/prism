# Workflow: Flow Discovery

## Objective
Map the navigable screens and paths in an app feature area, producing a screen state graph.

## Inputs
- `feature_description`: what feature/area to explore
- `entry_point`: where to start (e.g., "hotel detail page", "home screen")
- `depth_limit`: max screens to visit (default: 20)

## Process

1. Launch app and navigate to entry point
2. Take screenshot + get UI tree
3. Identify all interactive elements (buttons, links, tabs, CTAs)
4. For each unique path not yet visited:
   a. Navigate to it
   b. Screenshot + UI tree
   c. Add to screen graph
   d. Identify new interactive elements
5. Backtrack when reaching dead ends
6. Stop at depth_limit or when no new screens found

## Loop Prevention
- Hash each screen's UI tree XML
- Do not revisit screens with matching hashes
- Detect modal loops (3 identical screens in a row = stop that branch)

## Output Format
```json
{
  "entry_point": "hotel_detail",
  "screens": [
    {
      "id": "hotel_detail_main",
      "hash": "abc123",
      "screenshot": "path/to/screenshot.png",
      "key_elements": ["Book Now CTA", "Gallery", "Reviews tab"],
      "reachable_from": ["search_results"],
      "leads_to": ["gallery_fullscreen", "reviews_tab", "booking_flow"]
    }
  ],
  "navigation_paths": [...]
}
```
