import { createBrowserRouter } from "react-router-dom";

import { ResourcesPage } from "@/pages/ResourcesPage";
import { ResultPage } from "@/pages/ResultPage";
import { ReviewPage } from "@/pages/ReviewPage";
import { UploadPage } from "@/pages/UploadPage";

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
]);
