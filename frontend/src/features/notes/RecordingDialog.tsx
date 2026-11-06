import RecordingView from "./RecordingView";
import {
  useRecordingController,
  type RecordingDialogProps,
} from "./useRecordingController";

export default function RecordingDialog(props: RecordingDialogProps) {
  const recording = useRecordingController(props);
  return <RecordingView {...recording} />;
}
