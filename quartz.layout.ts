import { PageLayout, SharedLayout } from "./quartz/cfg"
import * as Component from "./quartz/components"

// components shared across all pages
export const sharedPageComponents: SharedLayout = {
  head: Component.Head(),
  header: [],
  afterBody: [],
  footer: Component.Footer({
    links: {
      GitHub: "https://github.com/jackyzha0/quartz",
      "Discord Community": "https://discord.gg/cRFFHYye7t",
    },
  }),
}

// components for pages that display a single page (e.g. a single note)
export const defaultContentPageLayout: PageLayout = {
  beforeBody: [
    Component.ConditionalRender({
      component: Component.Breadcrumbs(),
      condition: (page) => page.fileData.slug !== "index",
    }),
    Component.ArticleTitle(),
    Component.ContentMeta(),
    Component.TagList(),
  ],
  left: [
    Component.PageTitle(),
    Component.MobileOnly(Component.Spacer()),
    Component.Flex({
      components: [
        {
          Component: Component.Search(),
          grow: true,
        },
        { Component: Component.Darkmode() },
        { Component: Component.ReaderMode() },
      ],
    }),
    Component.Explorer(),
  ],
  right: [
    Component.Graph({
      localGraph: {
        showTags: false,       // show tag nodes in the graph
        depth: 2,              // hops from current page shown; 1 = direct neighbours only
        linkDistance: 50,      // target distance between linked nodes (px); default 30
        repelForce: 1,         // how strongly nodes push apart; default 0.5, higher = more spread
        fontSize: 1,         // label size multiplier; default 0.6
      },
      globalGraph: {
        showTags: false,       // show tag nodes in the graph
        depth: -1,             // -1 = show all nodes regardless of distance
        scale: 0.5,            // label scale multiplier; ~1.0 = normal, lower = larger labels
        linkDistance: 50,      // target distance between linked nodes (px); default 30
        repelForce: 1,       // how strongly nodes push apart; default 0.5, higher = more spread
        centerForce: 0.3,      // how strongly nodes pull toward center; default 0.2, keep low (0.1–1.0)
        fontSize: 0.5,         // label size multiplier; default 0.6
        opacityScale: 1.5,       // zoom level at which labels become visible; higher = need more zoom
        enableRadial: true
      },
    }),
    Component.DesktopOnly(Component.TableOfContents()),
    Component.Backlinks(),
  ],
}

// components for pages that display lists of pages  (e.g. tags or folders)
export const defaultListPageLayout: PageLayout = {
  beforeBody: [Component.Breadcrumbs(), Component.ArticleTitle(), Component.ContentMeta()],
  left: [
    Component.PageTitle(),
    Component.MobileOnly(Component.Spacer()),
    Component.Flex({
      components: [
        {
          Component: Component.Search(),
          grow: true,
        },
        { Component: Component.Darkmode() },
      ],
    }),
    Component.Explorer(),
  ],
  right: [],
}
