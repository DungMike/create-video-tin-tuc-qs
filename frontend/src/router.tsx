import { createBrowserRouter, Navigate } from "react-router-dom";

import { StoryEditStylesPage } from "@/pages/StoryEditStylesPage";
import { StoryVideoPage } from "@/pages/StoryVideoPage";
import { StoryVideoSettingsPage } from "@/pages/StoryVideoSettingsPage";

export const router = createBrowserRouter([
  {
    path: "/story-video",
    element: <StoryVideoPage />,
  },
  {
    path: "/story-video/settings",
    element: <StoryVideoSettingsPage />,
  },
  {
    path: "/story-video/edit-styles",
    element: <StoryEditStylesPage />,
  },
  // Cac luong cu (upload/review/batch-pipeline/news-bulletin/...) da bi go bo.
  // Moi URL khac deu quay ve trang chinh thay vi tra ra trang trang.
  {
    path: "*",
    element: <Navigate to="/story-video" replace />,
  },
]);
