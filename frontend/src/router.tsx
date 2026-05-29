import { createBrowserRouter } from "react-router-dom";

import { BatchPipelinePage } from "@/pages/BatchPipelinePage";
import { ChannelManagerPage } from "@/pages/ChannelManagerPage";
import { DecorLibraryPage } from "@/pages/DecorLibraryPage";
import { DocsToAudioPage } from "@/pages/DocsToAudioPage";
import { EffectsLibraryPage } from "@/pages/EffectsLibraryPage";
import { NewsBulletinPage } from "@/pages/NewsBulletinPage";
import { ResourcesPage } from "@/pages/ResourcesPage";
import { ResultPage } from "@/pages/ResultPage";
import { ReviewPage } from "@/pages/ReviewPage";
import { StoryVideoPage } from "@/pages/StoryVideoPage";
import { StoryVideoSettingsPage } from "@/pages/StoryVideoSettingsPage";
import { UploadPage } from "@/pages/UploadPage";
import { VoicesPage } from "@/pages/VoicesPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <UploadPage />,
  },
  {
    path: "/jobs/:jobId/review",
    element: <ReviewPage />,
  },
  {
    path: "/jobs/:jobId/resources",
    element: <ResourcesPage />,
  },
  {
    path: "/jobs/:jobId/result",
    element: <ResultPage />,
  },
  {
    path: "/effects-library",
    element: <EffectsLibraryPage />,
  },
  {
    path: "/docs-to-audio",
    element: <DocsToAudioPage />,
  },
  {
    path: "/voices",
    element: <VoicesPage />,
  },
  {
    path: "/decor-library",
    element: <DecorLibraryPage />,
  },
  {
    path: "/batch-pipeline",
    element: <BatchPipelinePage />,
  },
  {
    path: "/batch-pipeline/:pipelineId",
    element: <BatchPipelinePage />,
  },
  {
    path: "/news-bulletin",
    element: <NewsBulletinPage />,
  },
  {
    path: "/news-bulletin/:bulletinId",
    element: <NewsBulletinPage />,
  },
  {
    path: "/channels",
    element: <ChannelManagerPage />,
  },
  {
    path: "/story-video",
    element: <StoryVideoPage />,
  },
  {
    path: "/story-video/settings",
    element: <StoryVideoSettingsPage />,
  },
]);

