import cv2

def show_boxes(image_path, boxes):
    img = cv2.imread(image_path)
    for (x1, y1, x2, y2) in boxes:
        cv2.rectangle(img, (x1,y1), (x2,y2), (0,255,0), 2)
    cv2.imshow("Detections", img)
    cv2.waitKey(0)
    cv2.destroyAllWindows()
