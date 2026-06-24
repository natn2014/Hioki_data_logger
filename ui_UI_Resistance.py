# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'UI_ResistancelQEbwa.ui'
##
## Created by: Qt User Interface Compiler version 5.14.1
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide6.QtCore import (QCoreApplication, QMetaObject, QObject, QPoint,
    QRect, QSize, QUrl, Qt)
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QCursor, QFont,
    QFontDatabase, QIcon, QLinearGradient, QPalette, QPainter, QPixmap,
    QRadialGradient)
from PySide6.QtWidgets import (QAbstractSpinBox, QDoubleSpinBox, QFrame,
    QGroupBox, QHBoxLayout, QLabel, QListView, QPushButton, QSizePolicy,
    QVBoxLayout)


class Ui_Dialog(object):
    def setupUi(self, Dialog):
        if Dialog.objectName():
            Dialog.setObjectName(u"Dialog")
        sizePolicy = QSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(Dialog.sizePolicy().hasHeightForWidth())
        Dialog.setSizePolicy(sizePolicy)
        self.verticalLayout = QVBoxLayout(Dialog)
        self.verticalLayout.setObjectName(u"verticalLayout")

        # ── Status bar (connection + model button) ────────────────────────────
        self.groupBox_status = QGroupBox(Dialog)
        self.groupBox_status.setObjectName(u"groupBox_status")
        font = QFont()
        font.setPointSize(12)
        self.groupBox_status.setFont(font)
        self.horizontalLayout_2 = QHBoxLayout(self.groupBox_status)
        self.horizontalLayout_2.setObjectName(u"horizontalLayout_2")

        # ── Left: USB + WiFi cards stacked vertically ─────────────────────────
        self.statusCardsLayout = QVBoxLayout()
        self.statusCardsLayout.setSpacing(4)

        self.frame_usb = QFrame(self.groupBox_status)
        self.frame_usb.setObjectName(u"frame_usb")
        self.frame_usb.setFrameShape(QFrame.Shape.StyledPanel)
        self.usb_card_layout = QVBoxLayout(self.frame_usb)
        self.usb_card_layout.setContentsMargins(6, 4, 6, 4)
        self.label_usb_status = QLabel(self.frame_usb)
        self.label_usb_status.setObjectName(u"label_usb_status")
        self.label_usb_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.usb_card_layout.addWidget(self.label_usb_status)
        self.statusCardsLayout.addWidget(self.frame_usb)

        self.frame_wifi = QFrame(self.groupBox_status)
        self.frame_wifi.setObjectName(u"frame_wifi")
        self.frame_wifi.setFrameShape(QFrame.Shape.StyledPanel)
        self.wifi_card_layout = QVBoxLayout(self.frame_wifi)
        self.wifi_card_layout.setContentsMargins(6, 4, 6, 4)
        self.label_wifi = QLabel(self.frame_wifi)
        self.label_wifi.setObjectName(u"label_wifi")
        self.label_wifi.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.wifi_card_layout.addWidget(self.label_wifi)
        self.statusCardsLayout.addWidget(self.frame_wifi)

        self.horizontalLayout_2.addLayout(self.statusCardsLayout)

        self.pushButton_model = QPushButton(self.groupBox_status)
        self.pushButton_model.setObjectName(u"pushButton_model")
        self.pushButton_model.setMinimumHeight(80)
        font1 = QFont()
        font1.setPointSize(25)
        self.pushButton_model.setFont(font1)
        self.horizontalLayout_2.addWidget(self.pushButton_model, 1)

        self.verticalLayout.addWidget(self.groupBox_status)

        # ── Resistance row ────────────────────────────────────────────────────
        # Layout: [Measured — wide, stretch=3] | divider | [Upper / Lower — stacked, stretch=1]
        self.groupBox_Resistance = QGroupBox(Dialog)
        self.groupBox_Resistance.setObjectName(u"groupBox_Resistance")
        self.groupBox_Resistance.setFont(font)
        self.horizontalLayout = QHBoxLayout(self.groupBox_Resistance)
        self.horizontalLayout.setObjectName(u"horizontalLayout")

        # ── Left: Measured value (dominant width) ─────────────────────────────
        self.groupBox_MeasureValue = QGroupBox(self.groupBox_Resistance)
        self.groupBox_MeasureValue.setObjectName(u"groupBox_MeasureValue")
        font4 = QFont()
        font4.setPointSize(24)
        font4.setBold(True)
        font4.setWeight(QFont.Weight.Bold)
        self.groupBox_MeasureValue.setFont(font4)
        self.verticalLayout_4 = QVBoxLayout(self.groupBox_MeasureValue)
        self.verticalLayout_4.setObjectName(u"verticalLayout_4")
        self.doubleSpinBox_Measure = QDoubleSpinBox(self.groupBox_MeasureValue)
        self.doubleSpinBox_Measure.setObjectName(u"doubleSpinBox_Measure")
        font5 = QFont()
        font5.setPointSize(72)
        font5.setBold(True)
        font5.setWeight(QFont.Weight.Bold)
        self.doubleSpinBox_Measure.setFont(font5)
        self.doubleSpinBox_Measure.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.doubleSpinBox_Measure.setDecimals(3)
        self.verticalLayout_4.addWidget(self.doubleSpinBox_Measure)
        self.horizontalLayout.addWidget(self.groupBox_MeasureValue, 3)  # stretch=3

        # ── Vertical divider ──────────────────────────────────────────────────
        self.line = QFrame(self.groupBox_Resistance)
        self.line.setObjectName(u"line")
        self.line.setFrameShape(QFrame.Shape.VLine)
        self.line.setFrameShadow(QFrame.Shadow.Sunken)
        self.horizontalLayout.addWidget(self.line)

        # ── Right: Upper Limit (top) + Lower Limit (bottom) ───────────────────
        self.right_limits_layout = QVBoxLayout()

        font_limit_group = QFont()
        font_limit_group.setPointSize(18)
        font_limit_spin = QFont()
        font_limit_spin.setPointSize(30)

        self.groupBox_UpperLimit = QGroupBox(self.groupBox_Resistance)
        self.groupBox_UpperLimit.setObjectName(u"groupBox_UpperLimit")
        self.groupBox_UpperLimit.setFont(font_limit_group)
        self.verticalLayout_5 = QVBoxLayout(self.groupBox_UpperLimit)
        self.verticalLayout_5.setObjectName(u"verticalLayout_5")
        self.doubleSpinBox_UpperLimit = QDoubleSpinBox(self.groupBox_UpperLimit)
        self.doubleSpinBox_UpperLimit.setObjectName(u"doubleSpinBox_UpperLimit")
        self.doubleSpinBox_UpperLimit.setFont(font_limit_spin)
        self.doubleSpinBox_UpperLimit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.doubleSpinBox_UpperLimit.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.PlusMinus)
        self.doubleSpinBox_UpperLimit.setDecimals(3)
        self.verticalLayout_5.addWidget(self.doubleSpinBox_UpperLimit)
        self.right_limits_layout.addWidget(self.groupBox_UpperLimit)

        self.groupBox_LowerLimit = QGroupBox(self.groupBox_Resistance)
        self.groupBox_LowerLimit.setObjectName(u"groupBox_LowerLimit")
        self.groupBox_LowerLimit.setFont(font_limit_group)
        self.verticalLayout_3 = QVBoxLayout(self.groupBox_LowerLimit)
        self.verticalLayout_3.setObjectName(u"verticalLayout_3")
        self.doubleSpinBox_lowerLimit = QDoubleSpinBox(self.groupBox_LowerLimit)
        self.doubleSpinBox_lowerLimit.setObjectName(u"doubleSpinBox_lowerLimit")
        self.doubleSpinBox_lowerLimit.setFont(font_limit_spin)
        self.doubleSpinBox_lowerLimit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.doubleSpinBox_lowerLimit.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.PlusMinus)
        self.doubleSpinBox_lowerLimit.setDecimals(3)
        self.verticalLayout_3.addWidget(self.doubleSpinBox_lowerLimit)
        self.right_limits_layout.addWidget(self.groupBox_LowerLimit)

        self.horizontalLayout.addLayout(self.right_limits_layout, 1)  # stretch=1

        self.verticalLayout.addWidget(self.groupBox_Resistance)

        # ── Judgement ─────────────────────────────────────────────────────────
        self.groupBox_Judge = QGroupBox(Dialog)
        self.groupBox_Judge.setObjectName(u"groupBox_Judge")
        self.groupBox_Judge.setFont(font)
        self.verticalLayout_2 = QVBoxLayout(self.groupBox_Judge)
        self.verticalLayout_2.setObjectName(u"verticalLayout_2")
        self.pushButton_Judgement = QPushButton(self.groupBox_Judge)
        self.pushButton_Judgement.setObjectName(u"pushButton_Judgement")
        font6 = QFont()
        font6.setPointSize(48)
        self.pushButton_Judgement.setFont(font6)
        self.verticalLayout_2.addWidget(self.pushButton_Judgement)
        self.verticalLayout.addWidget(self.groupBox_Judge)

        # ── Data Log ──────────────────────────────────────────────────────────
        self.groupBox = QGroupBox(Dialog)
        self.groupBox.setObjectName(u"groupBox")
        self.groupBox.setMinimumSize(QSize(0, 100))
        self.groupBox.setMaximumSize(QSize(16777215, 150))
        self.groupBox.setFont(font)
        self.verticalLayout_6 = QVBoxLayout(self.groupBox)
        self.verticalLayout_6.setObjectName(u"verticalLayout_6")
        self.listView_logger = QListView(self.groupBox)
        self.listView_logger.setObjectName(u"listView_logger")
        self.verticalLayout_6.addWidget(self.listView_logger)
        self.verticalLayout.addWidget(self.groupBox)

        self.retranslateUi(Dialog)
        QMetaObject.connectSlotsByName(Dialog)
    # setupUi

    def retranslateUi(self, Dialog):
        Dialog.setWindowTitle(QCoreApplication.translate("Dialog", u"Dialog", None))
        self.groupBox_status.setTitle(u"")
        #self.label_ConnectionStatus.setText(QCoreApplication.translate("Dialog", u"Connection Status", None))
        self.pushButton_model.setText(QCoreApplication.translate("Dialog", u"Model", None))
        self.label_usb_status.setText(QCoreApplication.translate("Dialog", u"○ USB  Disconnected", None))
        self.label_wifi.setText(QCoreApplication.translate("Dialog", u"WiFi  ---", None))
        self.groupBox_Resistance.setTitle(QCoreApplication.translate("Dialog", u"Resistance", None))
        self.groupBox_MeasureValue.setTitle(QCoreApplication.translate("Dialog", u"Measured", None))
        self.groupBox_UpperLimit.setTitle(QCoreApplication.translate("Dialog", u"Upper Limit", None))
        self.groupBox_LowerLimit.setTitle(QCoreApplication.translate("Dialog", u"Lower Limit", None))
        self.doubleSpinBox_lowerLimit.setSuffix("")
        self.groupBox_Judge.setTitle(QCoreApplication.translate("Dialog", u"Judgement", None))
        self.pushButton_Judgement.setText(QCoreApplication.translate("Dialog", u"PASS", None))
        self.groupBox.setTitle(QCoreApplication.translate("Dialog", u"Data Log", None))
    # retranslateUi
