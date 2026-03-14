# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'UI_ResistancelQEbwa.ui'
##
## Created by: Qt User Interface Compiler version 5.14.1
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide2.QtCore import (QCoreApplication, QMetaObject, QObject, QPoint,
    QRect, QSize, QUrl, Qt)
from PySide2.QtGui import (QBrush, QColor, QConicalGradient, QCursor, QFont,
    QFontDatabase, QIcon, QLinearGradient, QPalette, QPainter, QPixmap,
    QRadialGradient)
from PySide2.QtWidgets import (QAbstractSpinBox, QDoubleSpinBox, QFrame,
    QGroupBox, QHBoxLayout, QLabel, QListView, QPushButton, QSizePolicy,
    QVBoxLayout)


class Ui_Dialog(object):
    def setupUi(self, Dialog):
        if Dialog.objectName():
            Dialog.setObjectName(u"Dialog")
        Dialog.resize(1280, 800)
        sizePolicy = QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(Dialog.sizePolicy().hasHeightForWidth())
        Dialog.setSizePolicy(sizePolicy)
        self.verticalLayout = QVBoxLayout(Dialog)
        self.verticalLayout.setObjectName(u"verticalLayout")
        self.groupBox_status = QGroupBox(Dialog)
        self.groupBox_status.setObjectName(u"groupBox_status")
        font = QFont()
        font.setPointSize(12)
        self.groupBox_status.setFont(font)
        self.horizontalLayout_2 = QHBoxLayout(self.groupBox_status)
        self.horizontalLayout_2.setObjectName(u"horizontalLayout_2")
        self.label_ConnectionStatus = QLabel(self.groupBox_status)
        self.label_ConnectionStatus.setObjectName(u"label_ConnectionStatus")

        self.horizontalLayout_2.addWidget(self.label_ConnectionStatus)

        self.pushButton_model = QPushButton(self.groupBox_status)
        self.pushButton_model.setObjectName(u"pushButton_model")
        self.pushButton_model.setMinimumSize(QSize(800, 120))
        font1 = QFont()
        font1.setPointSize(25)
        self.pushButton_model.setFont(font1)

        self.horizontalLayout_2.addWidget(self.pushButton_model)


        self.verticalLayout.addWidget(self.groupBox_status)

        self.groupBox_Resistance = QGroupBox(Dialog)
        self.groupBox_Resistance.setObjectName(u"groupBox_Resistance")
        self.groupBox_Resistance.setFont(font)
        self.horizontalLayout = QHBoxLayout(self.groupBox_Resistance)
        self.horizontalLayout.setObjectName(u"horizontalLayout")
        self.groupBox_LowerLimit = QGroupBox(self.groupBox_Resistance)
        self.groupBox_LowerLimit.setObjectName(u"groupBox_LowerLimit")
        font2 = QFont()
        font2.setPointSize(24)
        self.groupBox_LowerLimit.setFont(font2)
        self.verticalLayout_3 = QVBoxLayout(self.groupBox_LowerLimit)
        self.verticalLayout_3.setObjectName(u"verticalLayout_3")
        self.doubleSpinBox_lowerLimit = QDoubleSpinBox(self.groupBox_LowerLimit)
        self.doubleSpinBox_lowerLimit.setObjectName(u"doubleSpinBox_lowerLimit")
        font3 = QFont()
        font3.setPointSize(38)
        self.doubleSpinBox_lowerLimit.setFont(font3)
        self.doubleSpinBox_lowerLimit.setAlignment(Qt.AlignCenter)
        self.doubleSpinBox_lowerLimit.setButtonSymbols(QAbstractSpinBox.PlusMinus)
        self.doubleSpinBox_lowerLimit.setDecimals(3)

        self.verticalLayout_3.addWidget(self.doubleSpinBox_lowerLimit)


        self.horizontalLayout.addWidget(self.groupBox_LowerLimit)

        self.line = QFrame(self.groupBox_Resistance)
        self.line.setObjectName(u"line")
        self.line.setFrameShape(QFrame.VLine)
        self.line.setFrameShadow(QFrame.Sunken)

        self.horizontalLayout.addWidget(self.line)

        self.groupBox_MeasureValue = QGroupBox(self.groupBox_Resistance)
        self.groupBox_MeasureValue.setObjectName(u"groupBox_MeasureValue")
        self.groupBox_MeasureValue.setMinimumSize(QSize(670, 0))
        font4 = QFont()
        font4.setPointSize(24)
        font4.setBold(True)
        font4.setWeight(75)
        self.groupBox_MeasureValue.setFont(font4)
        self.verticalLayout_4 = QVBoxLayout(self.groupBox_MeasureValue)
        self.verticalLayout_4.setObjectName(u"verticalLayout_4")
        self.doubleSpinBox_Measure = QDoubleSpinBox(self.groupBox_MeasureValue)
        self.doubleSpinBox_Measure.setObjectName(u"doubleSpinBox_Measure")
        font5 = QFont()
        font5.setPointSize(48)
        font5.setBold(True)
        font5.setWeight(75)
        self.doubleSpinBox_Measure.setFont(font5)
        self.doubleSpinBox_Measure.setAlignment(Qt.AlignCenter)
        self.doubleSpinBox_Measure.setDecimals(3)

        self.verticalLayout_4.addWidget(self.doubleSpinBox_Measure)


        self.horizontalLayout.addWidget(self.groupBox_MeasureValue)

        self.line_2 = QFrame(self.groupBox_Resistance)
        self.line_2.setObjectName(u"line_2")
        self.line_2.setFrameShape(QFrame.VLine)
        self.line_2.setFrameShadow(QFrame.Sunken)

        self.horizontalLayout.addWidget(self.line_2)

        self.groupBox_UpperLimit = QGroupBox(self.groupBox_Resistance)
        self.groupBox_UpperLimit.setObjectName(u"groupBox_UpperLimit")
        self.groupBox_UpperLimit.setFont(font2)
        self.verticalLayout_5 = QVBoxLayout(self.groupBox_UpperLimit)
        self.verticalLayout_5.setObjectName(u"verticalLayout_5")
        self.doubleSpinBox_UpperLimit = QDoubleSpinBox(self.groupBox_UpperLimit)
        self.doubleSpinBox_UpperLimit.setObjectName(u"doubleSpinBox_UpperLimit")
        self.doubleSpinBox_UpperLimit.setFont(font3)
        self.doubleSpinBox_UpperLimit.setAlignment(Qt.AlignCenter)
        self.doubleSpinBox_UpperLimit.setButtonSymbols(QAbstractSpinBox.PlusMinus)
        self.doubleSpinBox_UpperLimit.setDecimals(3)

        self.verticalLayout_5.addWidget(self.doubleSpinBox_UpperLimit)


        self.horizontalLayout.addWidget(self.groupBox_UpperLimit)


        self.verticalLayout.addWidget(self.groupBox_Resistance)

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
        self.groupBox_status.setTitle(QCoreApplication.translate("Dialog", u"Status", None))
        self.label_ConnectionStatus.setText(QCoreApplication.translate("Dialog", u"Connection Status", None))
        self.pushButton_model.setText(QCoreApplication.translate("Dialog", u"Model", None))
        self.groupBox_Resistance.setTitle(QCoreApplication.translate("Dialog", u"Resistance", None))
        self.groupBox_LowerLimit.setTitle(QCoreApplication.translate("Dialog", u"Lower Limit", None))
        self.doubleSpinBox_lowerLimit.setSuffix("")
        self.groupBox_MeasureValue.setTitle(QCoreApplication.translate("Dialog", u"Measured ", None))
        self.groupBox_UpperLimit.setTitle(QCoreApplication.translate("Dialog", u"Upper Limit", None))
        self.groupBox_Judge.setTitle(QCoreApplication.translate("Dialog", u"Judgement", None))
        self.pushButton_Judgement.setText(QCoreApplication.translate("Dialog", u"PASS", None))
        self.groupBox.setTitle(QCoreApplication.translate("Dialog", u"Data Log", None))
    # retranslateUi

